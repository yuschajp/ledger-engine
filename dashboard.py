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


def build_html(conn, portfolio_id, as_of, ticker_lookup):
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

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(portfolio['name'])} - Ledger Engine Dashboard</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background: #f5f6f8; color: #1a1a1a; margin: 0; padding: 32px; }}
  h1 {{ color: #1B3A5C; margin-bottom: 4px; }}
  .subtitle {{ color: #5a5a5a; margin-bottom: 28px; }}
  .card {{ background: #ffffff; border-radius: 8px; padding: 20px 24px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .card h2 {{ color: #1B3A5C; font-size: 16px; margin-top: 0; border-bottom: 1px solid #e2e2e2; padding-bottom: 8px; }}
  .nav-figure {{ font-size: 28px; font-weight: 600; color: #1B3A5C; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; background: #1B3A5C; color: #fff; padding: 8px 10px; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #ececec; }}
  tr:nth-child(even) td {{ background: #fafafa; }}
</style>
</head>
<body>
  <h1>{html.escape(portfolio['name'])}</h1>
  <div class="subtitle">{html.escape(portfolio['base_currency'])} &middot; {html.escape(portfolio['strategy'] or '')} &middot; as of {as_of}</div>

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
</body>
</html>"""


def main():
    conn = init_db(":memory:")
    portfolio_id, as_of, ticker_lookup = build_pipeline_run(conn)
    output = build_html(conn, portfolio_id, as_of, ticker_lookup)
    with open("dashboard.html", "w") as f:
        f.write(output)
    print("Wrote dashboard.html -- open it in your browser to view it.")


if __name__ == "__main__":
    main()
