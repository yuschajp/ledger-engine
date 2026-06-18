"""
End-to-end demo of the ledger engine: create a portfolio and security,
book a buy transaction, derive a position from the transaction history,
and post the corresponding GL entries.

Run with: python3 demo.py
"""

from ledger_engine import init_db, insert_transaction, recompute_position, post_trade_entries


def main():
    conn = init_db(":memory:")

    conn.execute(
        "INSERT INTO portfolios (name, base_currency, strategy, inception_date) VALUES (?, ?, ?, ?)",
        ("Demo Multi-Strategy Fund", "USD", "Relative Value", "2024-01-01"),
    )
    portfolio_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO securities (ticker, instrument_type, currency) VALUES (?, ?, ?)",
        ("ACME", "equity", "USD"),
    )
    security_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO gl_accounts (account_number, account_name, account_type) VALUES (?, ?, ?)",
        ("1000", "Cash", "asset"),
    )
    cash_account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO gl_accounts (account_number, account_name, account_type) VALUES (?, ?, ?)",
        ("1100", "Securities", "asset"),
    )
    security_account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    txn = insert_transaction(
        conn, portfolio_id, security_id,
        trade_date="2024-06-01", settle_date="2024-06-03",
        transaction_type="buy", quantity=1000, price=25.50, commission=10.0,
    )

    post_trade_entries(conn, txn, cash_account_id, security_account_id)
    position = recompute_position(conn, portfolio_id, security_id, as_of="2024-06-01")

    print(f"Position as of 2024-06-01: {position['quantity']:.0f} units, "
          f"cost basis ${position['cost_basis']:,.2f}")

    print("\nGL entries posted:")
    for row in conn.execute("SELECT * FROM gl_entries ORDER BY gl_entry_id"):
        side = "DR" if row["debit_amount"] else "CR"
        amount = row["debit_amount"] or row["credit_amount"]
        print(f"  {side} ${amount:,.2f}  -  {row['description']}")


if __name__ == "__main__":
    main()
