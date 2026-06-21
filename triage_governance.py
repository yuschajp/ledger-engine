"""
Performance and governance layer on top of the triage agent: runs it
against a larger, more varied batch of reconciliation breaks with known
ground truth, simulates the human review every proposal has to pass
through, and computes the metrics an AI product manager would actually
want to see -- not "did the agent produce an answer" but "how often is
it right, where does it break down, and is the human gate actually
catching the mistakes."

The scenarios here are synthetic and seeded for reproducibility, not
measured production data. The point is to demonstrate the methodology --
how you'd structure an evaluation and a governance layer for a
human-in-the-loop AI feature -- not to report real performance numbers.
"""

import random

from triage_agent import _heuristic_proposal

# Ground-truth categories a break can actually belong to.
TRUE_ROUNDING = "rounding"
TRUE_TIMING_LAG = "timing_lag"
TRUE_CORPORATE_ACTION = "corporate_action"
TRUE_GENUINE_ERROR = "genuine_error"

CATEGORY_LABELS = {
    TRUE_ROUNDING: "Rounding / lot-size convention",
    TRUE_TIMING_LAG: "Settlement timing lag",
    TRUE_CORPORATE_ACTION: "Corporate action / unbooked trade",
    TRUE_GENUINE_ERROR: "Genuine trade error",
}


def _heuristic_category(break_row: dict) -> str:
    """Map the heuristic agent's actual decision branch to a ground-truth
    category, mirroring _heuristic_proposal's own logic exactly so it can
    be scored without parsing free text."""
    break_type = break_row["break_type"]
    if break_type == "missing_custodian":
        return TRUE_TIMING_LAG
    if break_type == "missing_internal":
        return TRUE_CORPORATE_ACTION
    internal_qty = break_row["internal_quantity"] or 0
    custodian_qty = break_row["custodian_quantity"] or 0
    diff = internal_qty - custodian_qty
    pct = abs(diff) / custodian_qty * 100 if custodian_qty else None
    if pct is not None and pct < 1:
        return TRUE_ROUNDING
    return TRUE_GENUINE_ERROR


def generate_scenarios(n_easy: int = 30, n_hard: int = 10, seed: int = 42) -> list:
    """Build a synthetic batch of breaks with known ground truth.

    'Easy' scenarios are cases the heuristic's own logic is designed to
    get right: clean instances of each break type, and quantity breaks
    with a percentage difference clearly on one side of the 1% threshold
    it uses to separate rounding from a real error.

    'Hard' scenarios are the heuristic's actual blind spot: very small
    positions where that 1% threshold misfires in both directions -- a
    real trade error that happens to be a small percentage of a small
    position, and a benign lot-size rounding difference that happens to
    be a large percentage of a small position.
    """
    rng = random.Random(seed)
    scenarios = []

    for i in range(n_easy):
        bucket = i % 4
        if bucket == 0:
            scenarios.append({
                "true_cause": TRUE_TIMING_LAG,
                "break_type": "missing_custodian",
                "internal_quantity": rng.choice([500, 1000, 2500, 10000]),
                "custodian_quantity": None,
            })
        elif bucket == 1:
            scenarios.append({
                "true_cause": TRUE_CORPORATE_ACTION,
                "break_type": "missing_internal",
                "internal_quantity": None,
                "custodian_quantity": rng.choice([200, 750, 1500, 4000]),
            })
        elif bucket == 2:
            custodian_qty = rng.choice([10000, 25000, 50000])
            diff = custodian_qty * rng.uniform(0.001, 0.005)  # well under 1%
            scenarios.append({
                "true_cause": TRUE_ROUNDING,
                "break_type": "quantity_break",
                "internal_quantity": custodian_qty + diff,
                "custodian_quantity": custodian_qty,
            })
        else:
            custodian_qty = rng.choice([10000, 25000, 50000])
            diff = custodian_qty * rng.uniform(0.03, 0.15)  # well over 1%
            scenarios.append({
                "true_cause": TRUE_GENUINE_ERROR,
                "break_type": "quantity_break",
                "internal_quantity": custodian_qty + diff,
                "custodian_quantity": custodian_qty,
            })

    for i in range(n_hard):
        if i % 2 == 0:
            # A real trade error on a small position: the absolute size
            # (one extra odd lot) is genuine, but it's under 1% of a small
            # custodian quantity, so the heuristic misreads it as rounding.
            custodian_qty = rng.choice([60, 80, 120])
            diff = rng.choice([1, 2])
            scenarios.append({
                "true_cause": TRUE_GENUINE_ERROR,
                "break_type": "quantity_break",
                "internal_quantity": custodian_qty + diff,
                "custodian_quantity": custodian_qty,
            })
        else:
            # A benign odd-lot rounding convention on a small position:
            # one share of difference is over 1% of a small custodian
            # quantity, so the heuristic misreads it as a genuine error.
            custodian_qty = rng.choice([40, 50, 70])
            diff = 1
            scenarios.append({
                "true_cause": TRUE_ROUNDING,
                "break_type": "quantity_break",
                "internal_quantity": custodian_qty + diff,
                "custodian_quantity": custodian_qty,
            })

    rng.shuffle(scenarios)
    for idx, scenario in enumerate(scenarios):
        scenario["scenario_id"] = idx + 1
    return scenarios


def _simulated_resolution_minutes(rng: random.Random, matched: bool) -> int:
    """Illustrative time-to-resolution, not measured data: decisions the
    human simply confirms are quick, overrides take longer because the
    reviewer has to dig in and figure out what actually happened."""
    if matched:
        return rng.randint(4, 12)
    return rng.randint(30, 75)


def evaluate_agent(scenarios: list, seed: int = 7) -> list:
    """Run the real heuristic proposal logic against every scenario, then
    simulate the human review each proposal has to pass through. Every
    record here is what would actually get logged to triage_decisions
    and reviewed by a person; nothing resolves itself."""
    rng = random.Random(seed)
    results = []
    for scenario in scenarios:
        proposal = _heuristic_proposal(scenario)
        predicted_cause = _heuristic_category(scenario)
        matched = predicted_cause == scenario["true_cause"]
        results.append({
            "scenario_id": scenario["scenario_id"],
            "break_type": scenario["break_type"],
            "true_cause": scenario["true_cause"],
            "predicted_cause": predicted_cause,
            "root_cause_text": proposal["root_cause"],
            "matched": matched,
            "review_outcome": "approved" if matched else "overridden",
            "resolution_minutes": _simulated_resolution_minutes(rng, matched),
        })
    return results


def compute_metrics(results: list) -> dict:
    total = len(results)
    matched = sum(1 for r in results if r["matched"])
    overridden = total - matched

    by_break_type = {}
    for r in results:
        bt = r["break_type"]
        by_break_type.setdefault(bt, {"total": 0, "matched": 0})
        by_break_type[bt]["total"] += 1
        if r["matched"]:
            by_break_type[bt]["matched"] += 1
    accuracy_by_break_type = {
        bt: stats["matched"] / stats["total"] for bt, stats in by_break_type.items()
    }

    avg_minutes_matched = (
        sum(r["resolution_minutes"] for r in results if r["matched"]) / max(matched, 1)
    )
    avg_minutes_overridden = (
        sum(r["resolution_minutes"] for r in results if not r["matched"]) / max(overridden, 1)
    )

    return {
        "total": total,
        "matched": matched,
        "overridden": overridden,
        "accuracy": matched / total,
        "override_rate": overridden / total,
        "accuracy_by_break_type": accuracy_by_break_type,
        "avg_minutes_matched": avg_minutes_matched,
        "avg_minutes_overridden": avg_minutes_overridden,
    }
