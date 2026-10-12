#!/usr/bin/env python3
"""
MLpractice/mlpractice_agentlog.py

Same RandomForest/XGBoost practice as mlpractice.py, but trained on the OTHER
CrushWalk telemetry stream: the per-agent DynamicAgentLogger CSV instead of
the per-link-tick dataset Crush3/bldataset.py builds.

Where mlpractice.py predicts crush_within_horizon (a forward-looking label
per LINK, per tick, built by aggregating many agents), this script predicts
agent_status (0=normal, 1=crushed) directly per AGENT, per tick -- the raw
label DynamicAgentLogger already writes, no windowing/aggregation needed.
Feature columns here are exactly Crush3/prop.json's "dynamic_logging.fields"
list (see AgentHandler -> DynamicAgentLogger.log() in patchCrowd.py), minus
link_id, which is a categorical identifier, not a numeric signal.

Reuses split_xy/train_and_report/walk_random_forest/walk_xgboost from
mlpractice.py instead of redefining them -- only the schema differs.
"""

from mlpractice import split_xy, train_and_report, walk_random_forest, walk_xgboost
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier

# Matches Crush3/prop.json's dynamic_logging.fields, minus link_id (an
# identifier string, not a feature -- same reason bldataset.py doesn't feed
# raw link_id into the model either).
FEATURE_NAMES = [
    "position",              # meters traveled along the current link
    "compression_pressure",  # N of chest-compression force this agent is under right now
    "current_speed",         # m/s, this tick
    "empty_speed",           # m/s this agent would walk at with nobody around (its baseline)
    "push_force",            # N, force being pushed onto this agent by neighbors
    "social_force",          # N, social-force repulsion magnitude acting on this agent
    "blocked_by",            # 1 if currently blocked by another agent, else 0
]

# 配列 (array) of toy per-agent-tick rows, standing in for real DynamicAgentLogger rows:
#   position, compression_pressure, current_speed, empty_speed, push_force,
#   social_force, blocked_by, agent_status
# agent_status: 0 = normal, 1 = crushed (this is the label being predicted --
# crushThreshold in Test.rb/patchCrowd.py is 1112.0 N, cf. Kroll et al. 2017)
DATA = [
    [2.0, 80.0,  1.20, 1.30, 40.0,  60.0,  0, 0],
    [5.5, 150.0, 1.05, 1.25, 90.0,  110.0, 0, 0],
    [9.0, 400.0, 0.75, 1.30, 220.0, 260.0, 1, 0],
    [12.0, 950.0, 0.20, 1.20, 700.0, 640.0, 1, 1],
    [13.5, 1180.0, 0.05, 1.25, 880.0, 720.0, 1, 1],
    [1.0, 60.0,  1.30, 1.30, 20.0,  40.0,  0, 0],
    [10.5, 850.0, 0.30, 1.15, 610.0, 590.0, 1, 1],
    [6.5, 210.0, 0.95, 1.20, 130.0, 150.0, 0, 0],
    [14.0, 1250.0, 0.02, 1.30, 910.0, 760.0, 1, 1],
    [3.0, 100.0, 1.15, 1.25, 55.0,  75.0,  0, 0],
    [11.0, 900.0, 0.25, 1.20, 660.0, 610.0, 1, 1],
    [7.0, 260.0, 0.85, 1.25, 160.0, 180.0, 0, 0],
]


def demo():
    X, y = split_xy(DATA)

    rf = train_and_report(
        RandomForestClassifier(n_estimators=200, random_state=0),
        "RandomForest (agent-level)", X, y, feature_names=FEATURE_NAMES,
    )
    xgb = train_and_report(
        XGBClassifier(n_estimators=200, random_state=0, eval_metric="logloss"),
        "XGBoost (agent-level)", X, y, feature_names=FEATURE_NAMES,
    )

    # A free-walking agent (low pressure/force, near its own empty_speed,
    # not blocked) should never be predicted crushed by either model.
    calm_row = [[2.0, 70.0, 1.25, 1.30, 30.0, 50.0, 0]]
    assert rf.predict(calm_row)[0] == 0, "RandomForest flagged a free-walking agent as crushed"
    assert xgb.predict(calm_row)[0] == 0, "XGBoost flagged a free-walking agent as crushed"
    print("\nsanity check passed: free-walking agent predicted as not-crushed by both models")

    # Walk through how each model calls one of the agent-rows above the
    # 1112 N crushThreshold (Kroll et al. 2017 sustained-compression figure).
    crushed_row = DATA[3][:-1]  # [12.0, 950.0, 0.20, 1.20, 700.0, 640.0, 1]
    walk_random_forest(rf, crushed_row, FEATURE_NAMES)
    walk_xgboost(xgb, crushed_row, FEATURE_NAMES)


if __name__ == "__main__":
    demo()
