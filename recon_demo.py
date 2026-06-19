"""
End-to-end demo of the reconciliation engine on top of the ledger engine.

Books two trades internally (ACME, TBOND), then loads a mock custodian
feed for the same date that doesn't match perfectly on purpose, so the
reconciliation engine has real breaks to find: a quantity mismatch, a
position the custodian doesn't show, and a position only the custodian
shows.

Run with: python3 recon_demo.py
"""

from ledger_engine import init_db, insert_transaction, recompute_position, post_trade_entries
from reconciliation_engine import insert_custodian_position, run_reconciliation


def setup_security(conn, ticker, instrument_type="equity"):
    conn.execute(
        "INSERT INTO securities (ticker, instrument_type, currency) VALUES (?, ?, 'USD')",
        (ticker, instrument_type),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def main():
    conn = init_db(":memory:")
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

    # Internal trades booked through the ledger engine
    acme_txn = insert_transaction(conn, portfolio_id, acme_id, as_of, as_of, "buy", 1000, 25.50, 10.0)
    post_trade_entries(conn, acme_txn, cash_id, sec_acct_id)
    recompute_position(conn, portfolio_id, acme_id, as_of)

    tbond_txn = insert_transaction(conn, portfolio_id, tbond_id, as_of, as_of, "buy", 500, 98.00, 5.0)
    post_trade_entries(conn, tbond_txn, cash_id, sec_acct_id)
    recompute_position(conn, portfolio_id, tbond_id, as_of)

    # Mock custodian feed for the same date -- deliberately doesn't match:
    #   ACME: custodian shows 950 instead of our 1000        -> quantity_break
    #   GLOB: custodian shows a position we never booked      -> missing_internal
    #   TBOND: we hold it, but it's absent from the custodian feed -> missing_custodian
    insert_custodian_position(conn, portfolio_id, acme_id, as_of, quantity=950)
    insert_custodian_position(conn, portfolio_id, glob_id, as_of, quantity=200)

    breaks = run_reconciliation(conn, portfolio_id, as_of)

    ticker_lookup = {acme_id: "ACME", tbond_id: "TBOND", glob_id: "GLOB"}
    print(f"Reconciliation run for {as_of}: {len(breaks)} break(s) found\n")
    for b in breaks:
        ticker = ticker_lookup.get(b["security_id"], b["security_id"])
        print(f"  [{b['break_type']}] {ticker}: internal={b['internal_quantity']}, "
              f"custodian={b['custodian_quantity']}")


if __name__ == "__main__":
    main()
