"""
Runs both PnL anomaly detectors against the same synthetic 250-day
series with known ground truth and reports precision, recall, and F1
for each side by side, rather than presenting either one as if it were
obviously the right choice.

This is the same evaluation habit as governance_demo.py applied to a
different risk surface: don't just show a model working, show what it
catches, what it misses, and where it costs you in false positives.
"""

from pnl_anomaly_model import run_comparison


def print_metrics(label: str, metrics: dict):
    print(f"\n{label}")
    print(f"  True positives:  {metrics['true_positives']}")
    print(f"  False positives: {metrics['false_positives']}")
    print(f"  False negatives: {metrics['false_negatives']}")
    print(f"  Precision: {metrics['precision']:.1%}   Recall: {metrics['recall']:.1%}   F1: {metrics['f1']:.2f}")


def main():
    result = run_comparison(n_days=250, seed=11)
    df = result["df"]

    print("=" * 72)
    print("PNL ANOMALY DETECTION COMPARISON")
    print("(synthetic, seeded data -- demonstrates the evaluation")
    print(" methodology, not measured production performance)")
    print("=" * 72)

    print(f"\nEvaluable days: {len(df)}")
    print(f"True anomalies (data entry errors): {int(df['true_anomaly'].sum())}")
    print(f"Legitimate high-volatility days (not anomalies): {int((df['day_type'] == 'legit_high_vol').sum())}")

    print_metrics("Rolling robust z-score (interpretable baseline):", result["zscore_metrics"])
    print_metrics("Isolation Forest (unsupervised ML):", result["iforest_metrics"])

    print("\nFlagged by the z-score detector:")
    flagged_z = df[df["zscore_flag"]][["date", "pnl", "day_type", "robust_zscore"]]
    for _, row in flagged_z.iterrows():
        date_str = row["date"].strftime("%Y-%m-%d")
        print(f"  {date_str}  ${row['pnl']:>14,.0f}  {row['day_type']:<16}  z={row['robust_zscore']:+.2f}")

    print("\nFlagged by the Isolation Forest detector:")
    flagged_if = df[df["iforest_flag"]][["date", "pnl", "day_type", "roll_std"]]
    for _, row in flagged_if.iterrows():
        date_str = row["date"].strftime("%Y-%m-%d")
        print(f"  {date_str}  ${row['pnl']:>14,.0f}  {row['day_type']:<16}  trailing vol=${row['roll_std']:,.0f}")

    print("\n" + "=" * 72)
    print("PRODUCT TAKEAWAY")
    print("=" * 72)
    print(
        "Both detectors catch the same share of genuine data errors here.\n"
        "The Isolation Forest doesn't catch more of them, but it does flag\n"
        "more false positives, a run of ordinary days that happened to sit\n"
        "inside an elevated-volatility stretch, because its second feature\n"
        "is the recent rolling volatility itself, not just the day's PnL.\n"
        "That's a real, specific cost of the more sophisticated model, not\n"
        "a reason to throw it out, but a reason to either feed it better\n"
        "features or pair it with the simpler detector as a sanity check,\n"
        "and to never let either one resolve anything without a person\n"
        "looking at the flagged day first."
    )


if __name__ == "__main__":
    main()
