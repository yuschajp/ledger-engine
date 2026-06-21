"""
PnL anomaly detection: a statistical model trained on a portfolio's daily
PnL history to flag days worth investigating, a different and
complementary risk surface from the reconciliation engine. Reconciliation
catches a quantity mismatch between the internal book and a custodian
feed; this catches a PnL print that doesn't look like the rest of the
series, regardless of whether the position quantities ever disagreed
with anyone.

Unlike the other three engines in this portfolio, this module uses
numpy, pandas, and scikit-learn rather than being dependency-free. That's
a deliberate choice, not an inconsistency: the other engines are
deterministic financial math (bond pricing, Black-Scholes, margin
calculations) that never needed a library in the first place. Anomaly
detection is genuinely a statistical modeling problem, and hand-rolling
an Isolation Forest from scratch wouldn't be representative of how
you'd actually build this.

Two detectors are evaluated side by side against the same synthetic
ground truth, rather than presenting just one as if it were the only
reasonable choice:
  - a rolling robust z-score: simple, fast, fully interpretable, and the
    kind of thing you could explain to a risk committee in one sentence
  - an Isolation Forest trained on rolling features: a real unsupervised
    ML model, less interpretable, potentially more sensitive to
    multivariate patterns a single z-score can't see

Neither detector resolves anything on its own. The output of both is a
flagged day for a human to look into, consistent with every other "AI"
layer in this portfolio: propose, never resolve.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

ROLLING_WINDOW = 20  # trading days


def generate_pnl_series(n_days: int = 250, seed: int = 11) -> pd.DataFrame:
    """Build a synthetic daily PnL series with three kinds of days:

    - normal: PnL ~ N(mu, sigma), the ordinary case
    - legit_high_vol: PnL ~ N(mu, 3*sigma) on a handful of specific days,
      standing in for a real event like an earnings print or an FOMC
      decision -- statistically extreme, but not an error, and NOT a
      true anomaly for evaluation purposes
    - data_error: PnL replaced with an obviously wrong value (a
      duplicated booking or a decimal-place slip), an order of magnitude
      more extreme than even the legit high-vol days -- IS a true
      anomaly

    The point of including legit_high_vol days at all is that they sit
    close enough to data_error days in raw magnitude that no detector
    can perfectly separate them on a single day's PnL alone, which is
    realistic and is exactly why a human still has to look.
    """
    rng = np.random.default_rng(seed)
    mu, sigma = 2_000.0, 15_000.0

    pnl = rng.normal(mu, sigma, n_days)
    day_type = ["normal"] * n_days

    legit_high_vol_days = rng.choice(range(30, n_days), size=5, replace=False)
    for d in legit_high_vol_days:
        pnl[d] = rng.normal(mu, sigma * 3)
        day_type[d] = "legit_high_vol"

    remaining = [d for d in range(30, n_days) if day_type[d] == "normal"]
    data_error_days = rng.choice(remaining, size=4, replace=False)
    for d in data_error_days:
        sign = rng.choice([-1, 1])
        pnl[d] = sign * abs(rng.normal(mu, sigma)) * rng.uniform(8, 14)
        day_type[d] = "data_error"

    dates = pd.bdate_range("2024-01-02", periods=n_days)
    df = pd.DataFrame({
        "date": dates,
        "pnl": pnl,
        "day_type": day_type,
        "true_anomaly": [t == "data_error" for t in day_type],
    })
    return df


def add_rolling_features(df: pd.DataFrame, window: int = ROLLING_WINDOW) -> pd.DataFrame:
    """Trailing rolling median, MAD, and std -- shifted by one day so
    every feature only ever uses information available before that
    day's print, never the print itself. No lookahead."""
    df = df.copy()
    pnl_shifted = df["pnl"].shift(1)
    df["roll_median"] = pnl_shifted.rolling(window, min_periods=window).median()
    df["roll_mad"] = (
        pnl_shifted.rolling(window, min_periods=window)
        .apply(lambda x: np.median(np.abs(x - np.median(x))), raw=True)
    )
    df["roll_std"] = pnl_shifted.rolling(window, min_periods=window).std()
    df["robust_zscore"] = 0.6745 * (df["pnl"] - df["roll_median"]) / df["roll_mad"].replace(0, np.nan)
    return df


def rolling_zscore_detector(df: pd.DataFrame, threshold: float = 3.5) -> pd.Series:
    """Flag any day where the robust z-score against the trailing window
    exceeds the threshold in either direction."""
    return df["robust_zscore"].abs() > threshold


def isolation_forest_detector(df: pd.DataFrame, contamination: float = 0.035,
                                seed: int = 7) -> pd.Series:
    """Flag days using an Isolation Forest trained on each day's PnL
    alongside the trailing rolling volatility, so the model can learn
    what counts as unusual relative to the recent regime rather than
    against a single fixed threshold."""
    features = df[["pnl", "roll_std"]].copy()
    model = IsolationForest(contamination=contamination, random_state=seed)
    predictions = model.fit_predict(features)
    return pd.Series(predictions == -1, index=df.index)


def evaluate_detector(flags: pd.Series, true_anomaly: pd.Series) -> dict:
    flags = flags.fillna(False)
    true_positives = int((flags & true_anomaly).sum())
    false_positives = int((flags & ~true_anomaly).sum())
    false_negatives = int((~flags & true_anomaly).sum())
    true_negatives = int((~flags & ~true_anomaly).sum())

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) else 0.0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negatives": true_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def run_comparison(n_days: int = 250, seed: int = 11) -> dict:
    """Generate the synthetic series, run both detectors, and score each
    against the same ground truth."""
    df = generate_pnl_series(n_days=n_days, seed=seed)
    df = add_rolling_features(df, window=ROLLING_WINDOW)
    evaluable = df.iloc[ROLLING_WINDOW:].copy()

    zscore_flags = rolling_zscore_detector(evaluable)
    iforest_flags = isolation_forest_detector(evaluable)

    evaluable["zscore_flag"] = zscore_flags
    evaluable["iforest_flag"] = iforest_flags

    return {
        "df": evaluable,
        "zscore_metrics": evaluate_detector(zscore_flags, evaluable["true_anomaly"]),
        "iforest_metrics": evaluate_detector(iforest_flags, evaluable["true_anomaly"]),
    }
