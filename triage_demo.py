"""
End-to-end demo of the AI triage agent layered on top of the reconciliation
engine: runs reconciliation, proposes a root cause and action for each open
break, and walks through a human approval gate for each one interactively.

Set ANTHROPIC_API_KEY to have Claude generate the proposals; without it,
the heuristic rules in triage_agent.py handle it with zero API calls.

Run with: python3 triage_demo.py
"""

from ledger_engine import init_db, insert_transaction, recompute_position, post_trade_entries
from reconciliation_engine import insert_custodian_position, run_reconciliation
from triage_agent import propose_resolution, log_triage_decision, approve_decision, reject_decision


def setup_security(conn, ticker, instrument_type="equity"):
    conn.execute(
        "INSERT INTO securities (ticker, instrument_type, currency) VALUES (?, ?, 'USD')",
        (ticker, instrument_type),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def build_demo_breaks(conn):
    as_of = "2024-06-01"
    conn.execute(
        "INSERT INTO portfolios (name, base_currency, strategy, inception_date) VALUES (?, ?, ?, ?)",
        ("Demo Multi-Strategy Fund", "USD", "Relative Value", "2024-01-01"),
    )
    portfolio_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    acme_id = setup_security(conn, "ACME", "equity")
    tbond_id = setup_security(conn, "TBOND", "bond")
    glob_id = setup_security(conn, "GLOB", "equity")

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

    ticker_lookup = {acme_id: "ACME", tbond_id: "TBOND", glob_id: "GLOB"}
    breaks = run_reconciliation(conn, portfolio_id, as_of)
    return breaks, ticker_lookup


def main():
    conn = init_db(":memory:")
    breaks, ticker_lookup = build_demo_breaks(conn)

    print(f"{len(breaks)} break(s) found. Reviewing each one:\n")

    for b in breaks:
        ticker = ticker_lookup.get(b["security_id"], b["security_id"])
        proposal = propose_resolution(b)
        decision_id = log_triage_decision(conn, b["break_id"], proposal)

        print(f"[{b['break_type']}] {ticker}  (internal={b['internal_quantity']}, "
              f"custodian={b['custodian_quantity']})")
        print(f"  Proposed by: {proposal['method']}")
        print(f"  Root cause: {proposal['root_cause']}")
        print(f"  Suggested action: {proposal['suggested_action']}")

        answer = input("  Approve this resolution? [y/n]: ").strip().lower()
        if answer == "y":
            approve_decision(conn, decision_id, reviewer="analyst")
            print("  -> Approved. Break marked resolved.\n")
        else:
            reject_decision(conn, decision_id, reviewer="analyst", reason="needs manual review")
            print("  -> Rejected. Break stays open for manual review.\n")

    print("Audit trail:")
    for row in conn.execute("SELECT * FROM triage_decisions ORDER BY decision_id"):
        print(f"  decision {row['decision_id']}: {row['status']} "
              f"(method={row['method']}, reviewed_by={row['reviewed_by']})")


if __name__ == "__main__":
    main()
