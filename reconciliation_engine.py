"""
Reconciliation engine: compares the internal ledger's positions against a
mock custodian feed and classifies any differences into break types.

This is the same logic that sits underneath custodian and prime broker
reconciliation in Geneva, Aladdin, or any institutional portfolio
accounting platform -- expressed here as plain code against the ledger
engine's own schema, with no external dependencies.
"""

from datetime import datetime, timezone

QUANTITY_TOLERANCE = 0.0001  # treat tiny float drift as a match, not a break


def insert_custodian_position(conn, portfolio_id, security_id, as_of_date,
                               quantity, market_value=None, source="custodian"):
    conn.execute(
        """INSERT INTO custodian_positions
           (portfolio_id, security_id, as_of_date, quantity, market_value, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (portfolio_id, security_id, as_of_date, quantity, market_value, source),
    )
    conn.commit()


def _record_break(conn, portfolio_id, security_id, as_of_date, break_type,
                   internal_qty, custodian_qty):
    diff = None
    if internal_qty is not None and custodian_qty is not None:
        diff = internal_qty - custodian_qty
    conn.execute(
        """INSERT INTO reconciliation_breaks
           (portfolio_id, security_id, as_of_date, break_type,
            internal_quantity, custodian_quantity, quantity_diff, status, detected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (portfolio_id, security_id, as_of_date, break_type,
         internal_qty, custodian_qty, diff, datetime.now(timezone.utc).isoformat()),
    )


def run_reconciliation(conn, portfolio_id: int, as_of_date: str) -> list:
    """Compare internal positions against the custodian feed for one portfolio/date.

    Three break types fall out of this comparison:
      - missing_custodian: we hold a security the custodian doesn't show
      - missing_internal: the custodian shows a security we don't have on our books
      - quantity_break: both sides have it, but the quantities don't match
    """
    internal_rows = conn.execute(
        """SELECT security_id, quantity FROM positions
           WHERE portfolio_id = ? AND as_of_date = ?""",
        (portfolio_id, as_of_date),
    ).fetchall()
    custodian_rows = conn.execute(
        """SELECT security_id, quantity FROM custodian_positions
           WHERE portfolio_id = ? AND as_of_date = ?""",
        (portfolio_id, as_of_date),
    ).fetchall()

    internal = {row["security_id"]: row["quantity"] for row in internal_rows}
    custodian = {row["security_id"]: row["quantity"] for row in custodian_rows}

    breaks = []
    for security_id in set(internal) | set(custodian):
        internal_qty = internal.get(security_id)
        custodian_qty = custodian.get(security_id)

        if internal_qty is not None and custodian_qty is None:
            break_type = "missing_custodian"
        elif internal_qty is None and custodian_qty is not None:
            break_type = "missing_internal"
        elif abs(internal_qty - custodian_qty) > QUANTITY_TOLERANCE:
            break_type = "quantity_break"
        else:
            continue  # matched -- no break

        _record_break(conn, portfolio_id, security_id, as_of_date, break_type,
                       internal_qty, custodian_qty)
        breaks.append({
            "security_id": security_id,
            "break_type": break_type,
            "internal_quantity": internal_qty,
            "custodian_quantity": custodian_qty,
        })

    conn.commit()
    return breaks
