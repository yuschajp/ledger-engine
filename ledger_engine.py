"""
Core ledger engine for a simplified portfolio accounting system.

Mirrors the IBOR -> ABOR pattern used by platforms like Geneva and Aladdin:
transactions are the source of truth, positions are a derived snapshot
rebuilt from transaction history (not stored as a running total you mutate
in place, which is what makes the system auditable), and GL entries are
the accounting-side postings generated from each transaction.

Built on sqlite3 from the standard library on purpose -- no external
dependencies, so it runs anywhere Python runs. Swap in SQLAlchemy or a
real Postgres connection later without changing the function signatures.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

BUY_LIKE = ("buy", "receive")


def init_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Create a fresh database (or in-memory connection) from schema.sql."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


@dataclass
class Transaction:
    transaction_id: int
    portfolio_id: int
    security_id: int
    trade_date: str
    transaction_type: str
    quantity: float
    price: float
    commission: float

    @property
    def signed_quantity(self) -> float:
        """Buys/receives increase the position, sells/delivers decrease it."""
        return self.quantity if self.transaction_type.lower() in BUY_LIKE else -self.quantity

    @property
    def gross_amount(self) -> float:
        return self.quantity * self.price + self.commission


def insert_transaction(conn, portfolio_id, security_id, trade_date, settle_date,
                        transaction_type, quantity, price, commission=0.0) -> Transaction:
    cur = conn.execute(
        """INSERT INTO transactions
           (portfolio_id, security_id, trade_date, settle_date, transaction_type,
            quantity, price, commission)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (portfolio_id, security_id, trade_date, settle_date, transaction_type,
         quantity, price, commission),
    )
    conn.commit()
    return Transaction(cur.lastrowid, portfolio_id, security_id, trade_date,
                        transaction_type, quantity, price, commission)


def recompute_position(conn, portfolio_id: int, security_id: int, as_of: str) -> dict:
    """Rebuild a position snapshot from the full transaction history as of a date."""
    rows = conn.execute(
        """SELECT transaction_type, quantity, price FROM transactions
           WHERE portfolio_id = ? AND security_id = ? AND trade_date <= ?""",
        (portfolio_id, security_id, as_of),
    ).fetchall()

    quantity = 0.0
    cost_basis = 0.0
    for row in rows:
        signed_qty = row["quantity"] if row["transaction_type"].lower() in BUY_LIKE else -row["quantity"]
        quantity += signed_qty
        cost_basis += signed_qty * row["price"]

    conn.execute(
        """INSERT INTO positions (portfolio_id, security_id, as_of_date, quantity, cost_basis)
           VALUES (?, ?, ?, ?, ?)""",
        (portfolio_id, security_id, as_of, quantity, cost_basis),
    )
    conn.commit()
    return {"quantity": quantity, "cost_basis": cost_basis}


def post_trade_entries(conn, txn: Transaction, cash_account_id: int, security_account_id: int):
    """Post the two-sided GL entries for a trade."""
    is_buy = txn.transaction_type.lower() in BUY_LIKE
    security_debit = txn.gross_amount if is_buy else 0.0
    security_credit = 0.0 if is_buy else txn.gross_amount
    cash_debit = security_credit
    cash_credit = security_debit

    conn.execute(
        """INSERT INTO gl_entries (transaction_id, gl_account_id, portfolio_id, entry_date,
                                    debit_amount, credit_amount, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (txn.transaction_id, security_account_id, txn.portfolio_id, txn.trade_date,
         security_debit, security_credit, f"{txn.transaction_type.title()} {txn.quantity:.0f} units"),
    )
    conn.execute(
        """INSERT INTO gl_entries (transaction_id, gl_account_id, portfolio_id, entry_date,
                                    debit_amount, credit_amount, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (txn.transaction_id, cash_account_id, txn.portfolio_id, txn.trade_date,
         cash_debit, cash_credit, f"Cash settlement for {txn.transaction_type}"),
    )
    conn.commit()
