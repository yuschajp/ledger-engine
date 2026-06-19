"""
AI triage agent for reconciliation breaks.

Reads each open break, proposes a root cause and a suggested resolution,
and logs the proposal with a full audit trail. Nothing gets auto-resolved:
every proposal sits in 'pending_review' until a human explicitly approves
or rejects it through approve_decision() / reject_decision(). That gate is
the actual point of this module, not the proposal text itself.

Two proposal methods are supported:
  - heuristic: rule-based reasoning over the break type and magnitude,
    works with zero external dependencies or API keys.
  - llm: calls the Claude API for a more nuanced read on the same break,
    used automatically if ANTHROPIC_API_KEY is set in the environment.

The LLM path uses urllib from the standard library rather than the
anthropic package, so the project still has zero pip dependencies even
with this layer enabled.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"


def _heuristic_proposal(break_row: dict) -> dict:
    """Rule-based root cause and suggested action -- no API call required."""
    break_type = break_row["break_type"]
    internal_qty = break_row["internal_quantity"]
    custodian_qty = break_row["custodian_quantity"]

    if break_type == "missing_custodian":
        return {
            "root_cause": "Position is booked internally but not yet reflected on the "
                           "custodian statement, most commonly a settlement timing lag.",
            "suggested_action": "Hold for one more settlement cycle, then escalate to the "
                                 "custodian if it's still missing.",
        }
    if break_type == "missing_internal":
        return {
            "root_cause": "Custodian reports a position with no matching internal trade, "
                           "often a corporate action, transfer, or unbooked trade.",
            "suggested_action": "Check for corporate action notices and unbooked trades on "
                                 "this security before assuming a custodian error.",
        }
    # quantity_break
    diff = (internal_qty or 0) - (custodian_qty or 0)
    pct = abs(diff) / custodian_qty * 100 if custodian_qty else None
    if pct is not None and pct < 1:
        root_cause = ("Small quantity difference, likely a rounding or lot-size convention "
                       "mismatch rather than a real trade discrepancy.")
    else:
        root_cause = ("Material quantity difference, consistent with a missed or duplicated "
                       "trade, or an unprocessed corporate action.")
    return {
        "root_cause": root_cause,
        "suggested_action": "Pull the trade blotter for this security and date and compare "
                             "line by line against the custodian statement.",
    }


def _llm_proposal(break_row: dict) -> dict:
    """Ask Claude for a root cause and suggested action for one break."""
    prompt = (
        "You are assisting a portfolio operations team in triaging a reconciliation break "
        "between an internal ledger and a custodian statement. Given the break below, respond "
        "with strict JSON only, no other text, in the form "
        '{"root_cause": "...", "suggested_action": "..."}. '
        "Keep each value to one or two sentences.\n\n"
        f"Break type: {break_row['break_type']}\n"
        f"Internal quantity: {break_row['internal_quantity']}\n"
        f"Custodian quantity: {break_row['custodian_quantity']}\n"
    )
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
    text = data["content"][0]["text"].strip()
    return json.loads(text)


def propose_resolution(break_row: dict) -> dict:
    """Propose a root cause and action for a break, using the LLM if a key is available.

    Falls back to the heuristic proposal if no key is set, or if the API
    call fails for any reason -- a break always gets a proposal either way.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            proposal = _llm_proposal(break_row)
            proposal["method"] = "llm"
            return proposal
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            proposal = _heuristic_proposal(break_row)
            proposal["method"] = "heuristic"
            proposal["llm_error"] = str(exc)
            return proposal
    proposal = _heuristic_proposal(break_row)
    proposal["method"] = "heuristic"
    return proposal


def log_triage_decision(conn, break_id: int, proposal: dict) -> int:
    """Log a proposed resolution as pending_review and return its decision_id."""
    cur = conn.execute(
        """INSERT INTO triage_decisions
           (break_id, method, root_cause, suggested_action, status, created_at)
           VALUES (?, ?, ?, ?, 'pending_review', ?)""",
        (break_id, proposal["method"], proposal["root_cause"], proposal["suggested_action"],
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def approve_decision(conn, decision_id: int, reviewer: str):
    """Human approval: marks the decision approved and the underlying break resolved."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE triage_decisions SET status='approved', reviewed_by=?, reviewed_at=?
           WHERE decision_id=?""",
        (reviewer, now, decision_id),
    )
    break_id = conn.execute(
        "SELECT break_id FROM triage_decisions WHERE decision_id=?", (decision_id,)
    ).fetchone()["break_id"]
    conn.execute("UPDATE reconciliation_breaks SET status='resolved' WHERE break_id=?", (break_id,))
    conn.commit()


def reject_decision(conn, decision_id: int, reviewer: str, reason: str = ""):
    """Human rejection: marks the decision rejected, leaves the break open for manual review."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE triage_decisions SET status='rejected', reviewed_by=?, reviewed_at=?
           WHERE decision_id=?""",
        (reviewer, now, decision_id),
    )
    conn.commit()
