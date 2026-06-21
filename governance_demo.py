"""
Runs the triage agent's heuristic proposal logic against a 40-scenario
synthetic batch with known ground truth, and reports the metrics an AI
product manager would want before trusting this gate with real volume:
overall accuracy, where accuracy breaks down by break type, the override
rate a human reviewer would actually see, and the time cost of catching
a mistake versus simply confirming a correct one.

This is the product-evaluation companion to triage_demo.py, which shows
the agent working correctly on three clean breaks. This script is built
specifically to also show it being wrong, since a governance layer that
only ever gets demoed on easy cases doesn't tell you anything about
whether the human gate is pulling its weight.
"""

from triage_governance import (
    CATEGORY_LABELS,
    compute_metrics,
    evaluate_agent,
    generate_scenarios,
)


def main():
    scenarios = generate_scenarios(n_easy=30, n_hard=10, seed=42)
    results = evaluate_agent(scenarios, seed=7)
    metrics = compute_metrics(results)

    print("=" * 72)
    print("TRIAGE AGENT GOVERNANCE REPORT")
    print("(synthetic, seeded data -- demonstrates the evaluation")
    print(" methodology, not measured production performance)")
    print("=" * 72)

    print(f"\nScenarios evaluated: {metrics['total']}")
    print(f"Agent proposal matched ground truth (approved as-is): {metrics['matched']}")
    print(f"Agent proposal overridden by human reviewer:          {metrics['overridden']}")
    print(f"Overall accuracy:     {metrics['accuracy']:.1%}")
    print(f"Override rate:        {metrics['override_rate']:.1%}")

    print("\nAccuracy by break type:")
    for break_type, acc in sorted(metrics["accuracy_by_break_type"].items()):
        print(f"  {break_type:<20} {acc:.1%}")

    print("\nTime to resolution (illustrative, not measured):")
    print(f"  Approved as-is:   {metrics['avg_minutes_matched']:.0f} min average")
    print(f"  Overridden:       {metrics['avg_minutes_overridden']:.0f} min average")
    print(f"  -> the human gate costs time exactly where it should: catching mistakes,")
    print(f"     not rubber-stamping correct calls.")

    overridden = [r for r in results if not r["matched"]]
    print(f"\nSample of overridden decisions ({len(overridden)} total):")
    for r in overridden[:4]:
        true_label = CATEGORY_LABELS[r["true_cause"]]
        predicted_label = CATEGORY_LABELS[r["predicted_cause"]]
        print(f"  Scenario {r['scenario_id']}: agent proposed '{predicted_label}', "
              f"actual cause was '{true_label}'")

    print("\n" + "=" * 72)
    print("PRODUCT TAKEAWAY")
    print("=" * 72)
    print(
        "The heuristic agent is reliable on clearly categorical breaks\n"
        "(missing_custodian, missing_internal) and on quantity breaks with\n"
        "an unambiguous percentage difference. Its blind spot is small\n"
        "positions, where a fixed 1% threshold doesn't separate a genuine\n"
        "error from benign rounding either way. That is exactly the kind\n"
        "of failure mode a human-in-the-loop gate exists to catch, and\n"
        "exactly the kind of finding that should drive the next model\n"
        "iteration, e.g. scaling the threshold to position size rather\n"
        "than using one fixed cutoff -- rather than removing the gate."
    )


if __name__ == "__main__":
    main()
