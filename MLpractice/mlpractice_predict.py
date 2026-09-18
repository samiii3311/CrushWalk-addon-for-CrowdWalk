#!/usr/bin/env python3
"""
MLpractice/mlpractice_predict.py

Loads the models mlpractice_combined.py already trained and saved (via
joblib) and runs them on new rows -- no retraining here. This is the split
a real pipeline would use: train once (expensive, done offline against the
full dataset), then predict many times cheaply by loading the saved model.

Run mlpractice_combined.py at least once first to create models/*.joblib.
"""

import joblib

from mlpractice_combined import FEATURE_NAMES, MODEL_DIR

# 配列 (array) of new/unlabeled rows to classify -- same column order as
# mlpractice_combined.FEATURE_NAMES, but with no crush_within_horizon label:
# that's exactly what real telemetry (or held-out test data) looks like
# before the model has made a call on it.
TEST_DATA = [
    # tick, position, density, density_trend, queue_count, bottleneck_ratio,
    #   compression_pressure, current_speed, empty_speed, push_force, social_force, blocked_by
    [15,  2.5, 1.4, 0.03, 0, 1.0, 90.0,  1.22, 1.28, 45.0,  65.0,  0],   # looks calm
    [145, 12.5, 4.9, 0.57, 10, 1.85, 990.0, 0.18, 1.20, 720.0, 650.0, 1],  # looks like the crush cases
]


def predict(model_path, rows):
    model = joblib.load(model_path)  # rebuild the exact fitted model object from disk
    preds = model.predict(rows)
    probas = model.predict_proba(rows)[:, 1]  # P(crush) for each row
    return preds, probas


def demo():
    if not MODEL_DIR.exists():
        raise SystemExit(f"no saved models in {MODEL_DIR}/ -- run mlpractice_combined.py first")

    for model_name, filename in [("RandomForest", "rf_combined.joblib"), ("XGBoost", "xgb_combined.joblib")]:
        preds, probas = predict(MODEL_DIR / filename, TEST_DATA)
        print(f"\n{model_name}:")
        for row, pred, proba in zip(TEST_DATA, preds, probas):
            labeled = ", ".join(f"{n}={v:g}" for n, v in zip(FEATURE_NAMES, row))
            verdict = "CRUSH" if pred else "no crush"
            print(f"  [{labeled}]\n    -> {verdict} (P(crush)={proba:.2f})")


if __name__ == "__main__":
    demo()
