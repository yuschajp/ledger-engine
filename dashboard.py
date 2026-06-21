"""
Generates a static HTML dashboard from a full run of the ledger engine,
reconciliation engine, and triage agent: positions, reconciliation breaks,
and the triage audit trail, all on one page you open directly in a
browser, no server required.

For this demo, breaks are auto-approved using the triage agent's
proposals so the report shows a complete, resolved pipeline. In a real
workflow, approve_decision() / reject_decision() would be called by an
actual human reviewer instead of being auto-approved here.

Run with: python3 dashboard.py
Then open dashboard.html in any browser.
"""

import html

from ledger_engine import init_db, insert_transaction, recompute_position, post_trade_entries
from reconciliation_engine import insert_custodian_position, run_reconciliation
from triage_agent import propose_resolution, log_triage_decision, approve_decision
from triage_governance import CATEGORY_LABELS, compute_metrics, evaluate_agent, generate_scenarios
from pnl_anomaly_model import run_comparison


def setup_security(conn, ticker, instrument_type="equity"):
    conn.execute(
        "INSERT INTO securities (ticker, instrument_type, currency) VALUES (?, ?, 'USD')",
        (ticker, instrument_type),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def build_pipeline_run(conn):
    as_of = "2024-06-01"
    conn.execute(
        "INSERT INTO portfolios (name, base_currency, strategy, inception_date) VALUES (?, ?, ?, ?)",
        ("Demo Multi-Strategy Fund", "USD", "Relative Value", "2024-01-01"),
    )
    portfolio_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    acme_id = setup_security(conn, "ACME", "equity")
    tbond_id = setup_security(conn, "TBOND", "bond")
    glob_id = setup_security(conn, "GLOB", "equity")
    ticker_lookup = {acme_id: "ACME", tbond_id: "TBOND", glob_id: "GLOB"}

    conn.execute("INSERT INTO gl_accounts (account_number, account_name, account_type) VALUES ('1000','Cash','asset')")
    cash_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO gl_accounts (account_number, account_name, account_type) VALUES ('1100','Securities','asset')")
    sec_acct_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    acme_txn = insert_transaction(conn, portfolio_id, acme_id, as_of, as_of, "buy", 1000, 25.50, 10.0)
    post_trade_entries(conn, acme_txn, cash_id, sec_acct_id)
    recompute_position(conn, portfolio_id, acme_id, as_of)

    tbond_txn = insert_transaction(conn, portfolio_id, tbond_id, as_of, as_of, "buy", 500, 98.00, 5.0)
    post_trade_entries(conn, tbond_txn, cash_id, sec_acct_id)
    recompute_position(conn, portfolio_id, tbond_id, as_of)

    insert_custodian_position(conn, portfolio_id, acme_id, as_of, quantity=950)
    insert_custodian_position(conn, portfolio_id, glob_id, as_of, quantity=200)

    breaks = run_reconciliation(conn, portfolio_id, as_of)
    for b in breaks:
        proposal = propose_resolution(b)
        decision_id = log_triage_decision(conn, b["break_id"], proposal)
        approve_decision(conn, decision_id, reviewer="dashboard-demo")

    return portfolio_id, as_of, ticker_lookup


def render_table(headers, rows):
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = ""
    for row in rows:
        cells = "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in row)
        body += f"<tr>{cells}</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_html(conn, portfolio_id, as_of, ticker_lookup, governance_results, governance_metrics,
                anomaly_result):
    portfolio = conn.execute(
        "SELECT name, base_currency, strategy FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)
    ).fetchone()

    nav_row = conn.execute(
        "SELECT SUM(cost_basis) AS nav FROM positions WHERE portfolio_id = ? AND as_of_date = ?",
        (portfolio_id, as_of),
    ).fetchone()
    nav_display = f"${nav_row['nav']:,.2f}" if nav_row["nav"] is not None else "n/a"

    positions = conn.execute(
        "SELECT security_id, quantity, cost_basis FROM positions WHERE portfolio_id = ? AND as_of_date = ?",
        (portfolio_id, as_of),
    ).fetchall()
    position_rows = [
        (ticker_lookup.get(p["security_id"], p["security_id"]), f"{p['quantity']:,.0f}", f"${p['cost_basis']:,.2f}")
        for p in positions
    ]

    breaks = conn.execute(
        """SELECT break_id, security_id, break_type, internal_quantity, custodian_quantity, status
           FROM reconciliation_breaks WHERE portfolio_id = ? AND as_of_date = ?""",
        (portfolio_id, as_of),
    ).fetchall()
    break_rows = [
        (b["break_id"], ticker_lookup.get(b["security_id"], b["security_id"]), b["break_type"],
         b["internal_quantity"], b["custodian_quantity"], b["status"])
        for b in breaks
    ]

    decisions = conn.execute(
        """SELECT decision_id, break_id, method, root_cause, suggested_action, status, reviewed_by
           FROM triage_decisions ORDER BY decision_id"""
    ).fetchall()
    decision_rows = [
        (d["decision_id"], d["break_id"], d["method"], d["root_cause"], d["suggested_action"],
         d["status"], d["reviewed_by"])
        for d in decisions
    ]

    overridden_rows = [
        (r["scenario_id"], r["break_type"], CATEGORY_LABELS[r["predicted_cause"]],
         CATEGORY_LABELS[r["true_cause"]], f"{r['resolution_minutes']} min")
        for r in governance_results if not r["matched"]
    ][:6]
    accuracy_rows = [
        (bt, f"{acc:.0%}") for bt, acc in sorted(governance_metrics["accuracy_by_break_type"].items())
    ]

    anomaly_df = anomaly_result["df"]
    z_metrics = anomaly_result["zscore_metrics"]
    if_metrics = anomaly_result["iforest_metrics"]
    detector_comparison_rows = [
        ("Rolling z-score (interpretable)", f"{z_metrics['precision']:.0%}", f"{z_metrics['recall']:.0%}", f"{z_metrics['f1']:.2f}"),
        ("Isolation Forest (ML)", f"{if_metrics['precision']:.0%}", f"{if_metrics['recall']:.0%}", f"{if_metrics['f1']:.2f}"),
    ]
    flagged_days_rows = []
    for _, row in anomaly_df[anomaly_df["zscore_flag"] | anomaly_df["iforest_flag"]].iterrows():
        flagged_days_rows.append((
            row["date"].strftime("%Y-%m-%d"),
            f"${row['pnl']:,.0f}",
            row["day_type"],
            "Yes" if row["zscore_flag"] else "No",
            "Yes" if row["iforest_flag"] else "No",
        ))

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(portfolio['name'])} - Ledger Engine Dashboard</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background: #f5f6f8; color: #1a1a1a; margin: 0; padding: 32px; }}
  h1 {{ color: #1B3A5C; margin-bottom: 4px; }}
  .subtitle {{ color: #5a5a5a; margin-bottom: 28px; }}
  .source-link {{ display: inline-block; margin-bottom: 20px; font-size: 13px; }}
  .source-link a {{ color: #1B3A5C; text-decoration: none; font-weight: 600; }}
  .source-link a:hover {{ text-decoration: underline; }}
  .card {{ background: #ffffff; border-radius: 8px; padding: 20px 24px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .card h2 {{ color: #1B3A5C; font-size: 16px; margin-top: 0; border-bottom: 1px solid #e2e2e2; padding-bottom: 8px; }}
  .nav-figure {{ font-size: 28px; font-weight: 600; color: #1B3A5C; }}
  .figure-row {{ }}
  .figure-block {{ display: inline-block; margin-right: 48px; vertical-align: top; }}
  .figure {{ font-size: 24px; font-weight: 600; color: #1B3A5C; }}
  .figure-warn {{ color: #b3401f; }}
  .figure-label {{ font-size: 12px; color: #5a5a5a; margin-bottom: 4px; }}
  .note {{ font-size: 13px; color: #5a5a5a; margin-bottom: 14px; }}
  .takeaway {{ font-size: 13px; line-height: 1.6; color: #2a2a2a; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; background: #1B3A5C; color: #fff; padding: 8px 10px; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #ececec; }}
  tr:nth-child(even) td {{ background: #fafafa; }}
</style>
</head>
<body>
  <h1>{html.escape(portfolio['name'])}</h1>
  <div class="subtitle">{html.escape(portfolio['base_currency'])} &middot; {html.escape(portfolio['strategy'] or '')} &middot; as of {as_of}</div>
  <div class="source-link"><a href="https://github.com/yuschajp/ledger-engine">View source on GitHub &rarr;</a></div>

  <div class="card">
    <h2>Net asset value (cost basis)</h2>
    <div class="nav-figure">{nav_display}</div>
  </div>

  <div class="card">
    <h2>Positions</h2>
    {render_table(["Security", "Quantity", "Cost basis"], position_rows)}
  </div>

  <div class="card">
    <h2>Reconciliation breaks</h2>
    {render_table(["Break ID", "Security", "Type", "Internal qty", "Custodian qty", "Status"], break_rows)}
  </div>

  <div class="card">
    <h2>Triage audit log</h2>
    {render_table(["Decision ID", "Break ID", "Method", "Root cause", "Suggested action", "Status", "Reviewed by"], decision_rows)}
  </div>

  <div class="card">
    <h2>Triage agent governance report</h2>
    <div class="note">Evaluated against a 40-scenario synthetic batch with known ground truth, seeded for reproducibility -- this demonstrates the evaluation methodology, not measured production performance.</div>
    <div class="figure-row">
      <div class="figure-block">
        <div class="figure-label">Scenarios evaluated</div>
        <div class="figure">{governance_metrics['total']}</div>
      </div>
      <div class="figure-block">
        <div class="figure-label">Overall accuracy</div>
        <div class="figure">{governance_metrics['accuracy']:.0%}</div>
      </div>
      <div class="figure-block">
        <div class="figure-label">Override rate</div>
        <div class="figure figure-warn">{governance_metrics['override_rate']:.0%}</div>
      </div>
      <div class="figure-block">
        <div class="figure-label">Avg. time: approved vs. overridden</div>
        <div class="figure">{governance_metrics['avg_minutes_matched']:.0f} / {governance_metrics['avg_minutes_overridden']:.0f} min</div>
      </div>
    </div>

    <div style="margin-top: 20px;"><strong style="font-size: 13px; color: #1B3A5C;">Accuracy by break type</strong></div>
    {render_table(["Break type", "Accuracy"], accuracy_rows)}

    <div style="margin-top: 20px;"><strong style="font-size: 13px; color: #1B3A5C;">Sample of overridden decisions</strong></div>
    {render_table(["Scenario", "Break type", "Agent proposed", "Actual cause", "Time to resolve"], overridden_rows)}

    <div style="margin-top: 20px;"><strong style="font-size: 13px; color: #1B3A5C;">Product takeaway</strong></div>
    <div class="takeaway">The heuristic agent is reliable on clearly categorical breaks and on quantity breaks with an unambiguous percentage difference. Its blind spot is small positions, where a single fixed 1% threshold doesn't separate a genuine error from benign rounding either way. That's exactly the kind of failure mode a human-in-the-loop gate exists to catch, and exactly the kind of finding that should drive the next model iteration &mdash; scaling the threshold to position size rather than using one fixed cutoff &mdash; rather than removing the gate.</div>
  </div>

  <div class="card">
    <h2>PnL anomaly detection: model comparison</h2>
    <div class="note">A different risk surface from reconciliation: this flags PnL prints that don't look like the rest of the series, regardless of whether quantities ever disagreed with anyone. Evaluated against a 250-day synthetic series with known ground truth, seeded for reproducibility.</div>

    {render_table(["Detector", "Precision", "Recall", "F1"], detector_comparison_rows)}

    <div style="margin-top: 20px;"><strong style="font-size: 13px; color: #1B3A5C;">Days flagged by either detector</strong></div>
    {render_table(["Date", "PnL", "Day type", "Z-score flagged", "Isolation Forest flagged"], flagged_days_rows)}

    <div style="margin-top: 20px;"><strong style="font-size: 13px; color: #1B3A5C;">Product takeaway</strong></div>
    <div class="takeaway">Both detectors catch the same share of genuine data errors. The Isolation Forest doesn't catch more of them, but it flags more false positives &mdash; a run of ordinary days that happened to sit inside an elevated-volatility stretch, because its second feature is the recent rolling volatility itself, not just the day's PnL. That's a specific, real cost of the more sophisticated model, not a reason to discard it, but a reason to feed it better features or pair it with the simpler detector as a sanity check &mdash; and to never let either one resolve anything without a person looking at the flagged day first.</div>
  </div>
</body>
</html>"""


def main():
    conn = init_db(":memory:")
    portfolio_id, as_of, ticker_lookup = build_pipeline_run(conn)

    scenarios = generate_scenarios(n_easy=30, n_hard=10, seed=42)
    governance_results = evaluate_agent(scenarios, seed=7)
    governance_metrics = compute_metrics(governance_results)

    anomaly_result = run_comparison(n_days=250, seed=11)

    output = build_html(conn, portfolio_id, as_of, ticker_lookup, governance_results, governance_metrics,
                         anomaly_result)
    with open("dashboard.html", "w") as f:
        f.write(output)
    print("Wrote dashboard.html -- open it in your browser to view it.")


if __name__ == "__main__":
    main()
