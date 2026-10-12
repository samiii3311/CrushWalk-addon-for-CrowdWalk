#!/usr/bin/env python3
"""
MLpractice/mlpractice_combined.py

Merges the two other practice scripts' feature sets into one row per
agent-tick, instead of picking link-level (mlpractice.py) or agent-level
(mlpractice_agentlog.py) alone. In the real pipeline this is exactly what
joining a LinkAggregateLogger row to a DynamicAgentLogger row on matching
(tick, link_id) would produce -- not a new logger, just both existing CSVs
combined on their shared key.

The three kinds of signal line up with "who / when / where":
  - WHO:   this agent's own physics right now (compression_pressure,
           current_speed, empty_speed, push_force, social_force, blocked_by)
           -- from DynamicAgentLogger, i.e. mlpractice_agentlog.py's schema.
  - WHEN:  tick -- how far into the run this is.
  - WHERE: position (precise spot along the link) plus the link's own
           congestion context (density, density_trend, queue_count,
           bottleneck_ratio) -- from LinkAggregateLogger / bldataset.py,
           i.e. mlpractice.py's schema.

Raw agent_id and link_id strings are deliberately left OUT of the feature
list -- they're identifiers, not signal (same reason link_id was dropped in
mlpractice_agentlog.py). "who" is represented by the agent's own physics
values, not by memorizing its ID string.

Reuses split_xy/train_and_report/walk_random_forest/walk_xgboost from
mlpractice.py -- only the schema (feature list + data) differs.
"""

from pathlib import Path

import joblib  # ships with scikit-learn; the standard way to save/load sklearn-style models
from mlpractice import split_xy, train_and_report, walk_random_forest, walk_xgboost
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier

# Where trained models get saved, so mlpractice_predict.py can load them back
# without retraining. Both RandomForestClassifier and XGBClassifier are plain
# picklable Python objects, so joblib.dump/load is all this needs.
MODEL_DIR = Path(__file__).parent / "models"

FEATURE_NAMES = [
    "tick",                  # WHEN: simulation tick this row was logged at
    "position",              # WHERE: meters along the current link
    "density",                # WHERE: agents/m^2 on this link right now
    "density_trend",          # WHERE: how fast that density is rising/falling
    "queue_count",             # WHERE: agents currently stopped/queued on this link
    "bottleneck_ratio",        # WHERE: widest upstream feeder link / this link's width
    "compression_pressure",  # WHO: N of chest-compression force on this agent right now
    "current_speed",         # WHO: this agent's speed this tick (m/s)
    "empty_speed",            # WHO: this agent's own free-flow baseline speed (m/s)
    "push_force",              # WHO: N being pushed onto this agent by neighbors
    "social_force",            # WHO: N of social-force repulsion acting on this agent
    "blocked_by",               # WHO: 1 if currently blocked by another agent, else 0
]

# 配列 (array): each row is one agent, at one tick, on one link -- the same 12
# scenarios as the other two practice scripts, just carrying both scripts'
# columns side by side instead of picking one view or the other.
DATA = [
    # tick, position, density, density_trend, queue_count, bottleneck_ratio,
    #   compression_pressure, current_speed, empty_speed, push_force, social_force, blocked_by, crush
    [5,   2.0,  1.2, 0.05, 0,  1.0, 80.0,   1.20, 1.30, 40.0,  60.0,  0, 0],
    [40,  5.5,  2.1, 0.10, 2,  1.1, 150.0,  1.05, 1.25, 90.0,  110.0, 0, 0],
    [90,  9.0,  3.4, 0.30, 5,  1.4, 400.0,  0.75, 1.30, 220.0, 260.0, 1, 0],
    [140, 12.0, 4.8, 0.55, 9,  1.8, 950.0,  0.20, 1.20, 700.0, 640.0, 1, 1],
    [160, 13.5, 5.5, 0.62, 12, 2.0, 1180.0, 0.05, 1.25, 880.0, 720.0, 1, 1],
    [10,  1.0,  1.5, -0.02, 0, 1.0, 60.0,   1.30, 1.30, 20.0,  40.0,  0, 0],
    [120, 10.5, 3.9, 0.40, 7,  1.6, 850.0,  0.30, 1.15, 610.0, 590.0, 1, 1],
    [60,  6.5,  2.6, 0.15, 3,  1.2, 210.0,  0.95, 1.20, 130.0, 150.0, 0, 0],
    [150, 14.0, 5.1, 0.58, 11, 1.9, 1250.0, 0.02, 1.30, 910.0, 760.0, 1, 1],
    [20,  3.0,  1.8, 0.01, 1,  1.0, 100.0,  1.15, 1.25, 55.0,  75.0,  0, 0],
    [130, 11.0, 4.2, 0.45, 8,  1.7, 900.0,  0.25, 1.20, 660.0, 610.0, 1, 1],
    [75,  7.0,  2.9, 0.20, 4,  1.3, 260.0,  0.85, 1.25, 160.0, 180.0, 0, 0],
]


def demo():
    X, y = split_xy(DATA)

    rf = train_and_report(
        RandomForestClassifier(n_estimators=200, random_state=0),
        "RandomForest (who+when+where)", X, y, feature_names=FEATURE_NAMES,
    )
    xgb = train_and_report(
        XGBClassifier(n_estimators=200, random_state=0, eval_metric="logloss"),
        "XGBoost (who+when+where)", X, y, feature_names=FEATURE_NAMES,
    )

    # Early tick, calm link, free-walking agent -- should never predict crush.
    calm_row = [[5, 2.0, 1.2, 0.0, 0, 1.0, 70.0, 1.25, 1.30, 30.0, 50.0, 0]]
    assert rf.predict(calm_row)[0] == 0, "RandomForest flagged a calm who/when/where row as crush"
    assert xgb.predict(calm_row)[0] == 0, "XGBoost flagged a calm who/when/where row as crush"
    print("\nsanity check passed: calm who/when/where row predicted as no-crush by both models")

    crush_row = DATA[4][:-1]  # worst row: latest tick, deepest into the link, highest pressure
    walk_random_forest(rf, crush_row, FEATURE_NAMES)
    walk_xgboost(xgb, crush_row, FEATURE_NAMES)

    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump(rf, MODEL_DIR / "rf_combined.joblib")
    joblib.dump(xgb, MODEL_DIR / "xgb_combined.joblib")
    print(f"\nsaved trained models to {MODEL_DIR}/ -- see mlpractice_predict.py to load and use them")


if __name__ == "__main__":
    demo()
