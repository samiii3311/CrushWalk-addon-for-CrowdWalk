#!/usr/bin/env python3
"""
MLpractice/mlpractice.py

Practice run of the two model families actually planned for the crush-prediction
sub-problem (see Crush3/crush_prediction_research_plan.md, "ML technique decision"):
Random Forest and XGBoost. Both are tree-ensemble classifiers, but they build
their trees differently:
  - Random Forest: trains many deep trees independently on random subsets of
    the data/features, then averages their votes ("bagging"). More resistant
    to overfitting on a small dataset, needs little tuning.
  - XGBoost: trains trees one at a time, where each new tree specifically
    targets the previous trees' mistakes ("boosting"). Usually wins on raw
    accuracy and is faster at inference, which matters if this ends up as the
    optimizer loop's inner-loop surrogate model later.

Both libraries are real open-source projects, not hand-rolled here:
  - scikit-learn: github.com/scikit-learn/scikit-learn (RandomForestClassifier)
  - XGBoost:      github.com/dmlc/xgboost (XGBClassifier)

Input is a toy 配列 (array) instead of a CSV -- one row per simulated link-tick,
using the exact feature schema Crush3/bldataset.py produces from real CrowdWalk
telemetry: density, density_trend, queue_count, bottleneck_ratio, each row
labeled with the forward-looking crush_within_horizon flag it predicts.
"""

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier

# Column names for the 4 input features, in the same order as each DATA row.
# These match Crush3/bldataset.py's output columns exactly, so a real dataset
# file could be swapped in for DATA below without changing anything else.
FEATURE_NAMES = ["density", "density_trend", "queue_count", "bottleneck_ratio"]

# 配列 (array) of toy training rows, standing in for real telemetry rows:
#   density         -- agents per square meter on the link right now
#   density_trend   -- how fast density is rising/falling (positive = getting worse)
#   queue_count     -- number of agents currently stopped/queued on the link
#   bottleneck_ratio -- width of the widest upstream feeder link / this link's width
#                        (>1 means agents are being funneled into a narrower space)
#   crush_within_horizon -- label: 1 if a crush event happens within the forecast
#                            window, 0 if not (this is what the model predicts)
DATA = [
    # density, density_trend, queue_count, bottleneck_ratio, crush_within_horizon
    [1.2, 0.05, 0, 1.0, 0],
    [2.1, 0.10, 2, 1.1, 0],
    [3.4, 0.30, 5, 1.4, 0],
    [4.8, 0.55, 9, 1.8, 1],
    [5.5, 0.62, 12, 2.0, 1],
    [1.5, -0.02, 0, 1.0, 0],
    [3.9, 0.40, 7, 1.6, 1],
    [2.6, 0.15, 3, 1.2, 0],
    [5.1, 0.58, 11, 1.9, 1],
    [1.8, 0.01, 1, 1.0, 0],
    [4.2, 0.45, 8, 1.7, 1],
    [2.9, 0.20, 4, 1.3, 0],
]


def split_xy(rows):
    """Split each [feature..., label] row into a feature list X and label list y.
    Works for any number of feature columns -- the label is always last.
    """
    X = [row[:-1] for row in rows]  # every column except the last: the model's inputs
    y = [row[-1] for row in rows]   # last column: the classification label
    return X, y


def train_and_report(model, name, X, y, feature_names=FEATURE_NAMES):
    """Fit a model on (X, y), then print its training accuracy and, if the
    model exposes one (both RandomForest and XGBoost do), its feature
    importances -- which of the inputs it leaned on most to make its calls.
    This is the cheap first answer to "which variables actually predict a
    crush", ahead of a proper SHAP pass on a real dataset later.
    """
    model.fit(X, y)                                        # train the model on the toy data
    preds = model.predict(X)                                # re-predict the training rows themselves
    importances = getattr(model, "feature_importances_", None)  # None if the model type doesn't expose this
    accuracy = sum(p == t for p, t in zip(preds, y)) / len(y)   # fraction of rows predicted correctly
    print(f"\n{name}")
    print(f"  train accuracy: {accuracy:.2f}")
    if importances is not None:
        # sort features by importance, highest first, so the printout reads best-to-worst
        ranked = sorted(zip(feature_names, importances), key=lambda kv: -kv[1])
        print("  feature importances:", ", ".join(f"{n}={v:.2f}" for n, v in ranked))
    return model


def walk_random_forest(rf, row, feature_names):
    """Show how ONE tree in the forest reaches its vote for `row`, then how
    the full forest's votes combine into a prediction. This is "bagging":
    every tree sees a slightly different random slice of the training data,
    each casts an independent vote, and the forest just takes the majority.
    """
    tree = rf.estimators_[0].tree_                            # look at just the first tree (of 200)
    node_indicator = rf.estimators_[0].decision_path([row])    # which nodes this row passes through
    leaf_id = rf.estimators_[0].apply([row])[0]                 # which node is the final leaf
    # decision_path returns a sparse matrix; pull out this one row's list of visited node ids
    path = node_indicator.indices[node_indicator.indptr[0]:node_indicator.indptr[1]]

    print(f"\nRandomForest walkthrough for row {row} (tree #1 of {len(rf.estimators_)}):")
    for node_id in path:
        if node_id == leaf_id:
            # reached the end of the path: read off how many training rows of each
            # class landed in this leaf, and vote for whichever class has more
            counts = tree.value[node_id][0]
            vote = "crush" if counts.argmax() == 1 else "no crush"
            print(f"    -> leaf reached: votes '{vote}' (class counts seen in training: {counts.astype(int)})")
        else:
            # an internal split node: figure out which feature/threshold it tests,
            # then check this row's actual value against it to see which way it goes
            feat = feature_names[tree.feature[node_id]]
            threshold = tree.threshold[node_id]
            value = row[tree.feature[node_id]]
            goes = "left (<=)" if value <= threshold else "right (>)"
            print(f"    is {feat}={value:g} <= {threshold:.2f}? -> goes {goes}")

    votes = [int(est.predict([row])[0]) for est in rf.estimators_]  # ask every tree for its own vote
    crush_votes = sum(votes)
    proba = rf.predict_proba([row])[0][1]  # forest's overall confidence = fraction of trees voting crush
    print(f"  all {len(votes)} trees: {crush_votes} voted crush, {len(votes) - crush_votes} voted no-crush")
    print(f"  forest prediction: {'crush' if rf.predict([row])[0] else 'no crush'} (P(crush)={proba:.2f})")


def walk_xgboost(xgb, row, feature_names):
    """Show how XGBoost's prediction shifts as more boosted trees are added.
    This is "boosting": tree #1 makes a rough guess, then every following
    tree is trained specifically to correct the errors the trees before it
    made, nudging the probability toward the right answer round by round.
    """
    n_trees = xgb.get_booster().num_boosted_rounds()  # how many trees were actually trained
    # pick a handful of round counts to sample the convergence at, capped to n_trees
    checkpoints = sorted(set(c for c in (1, 5, 10, 25, n_trees) if 1 <= c <= n_trees))

    labeled = ", ".join(f"{n}={v:g}" for n, v in zip(feature_names, row))
    print(f"\nXGBoost walkthrough for row [{labeled}] ({n_trees} boosting rounds total):")
    row_arr = np.array([row])  # xgboost expects a 2D array (rows x features), even for one row
    for k in checkpoints:
        # iteration_range=(0, k) asks XGBoost to only sum up the first k trees'
        # contributions, so this shows the running prediction as trees are added
        proba = xgb.predict_proba(row_arr, iteration_range=(0, k))[0][1]
        print(f"  after {k:>3} tree(s): P(crush) = {proba:.3f}")
    print(f"  final prediction: {'crush' if xgb.predict(row_arr)[0] else 'no crush'}")


def demo():
    X, y = split_xy(DATA)

    # n_estimators=200: number of independent trees to bag together. More trees
    # generally means smoother/more stable predictions, at a linear cost in
    # training time -- 200 is a common reasonable default, not tuned here.
    rf = train_and_report(
        RandomForestClassifier(n_estimators=200, random_state=0),
        "RandomForest", X, y,
    )

    # eval_metric="logloss": XGBoost needs an explicit metric in recent
    # versions; logloss is the standard choice for binary classification.
    # use_label_encoder isn't needed -- our labels are already plain 0/1 ints.
    xgb = train_and_report(
        XGBClassifier(n_estimators=200, random_state=0, eval_metric="logloss"),
        "XGBoost", X, y,
    )

    # Sanity check: a calm, uncrowded link (low density, flat trend, no queue,
    # no bottleneck) should never be predicted as crush-risk by either model.
    # This is the "smallest thing that fails if the logic breaks" check --
    # not a real test suite, just a tripwire for an obviously broken model.
    calm_row = [[1.0, 0.0, 0, 1.0]]
    assert rf.predict(calm_row)[0] == 0, "RandomForest flagged a calm link as crush-risk"
    assert xgb.predict(calm_row)[0] == 0, "XGBoost flagged a calm link as crush-risk"
    print("\nsanity check passed: calm link predicted as no-crush by both models")

    # Walk through how each model actually arrives at a crush call, using one
    # of the training rows that's labeled crush_within_horizon=1.
    crush_row = DATA[3][:4]  # [4.8, 0.55, 9, 1.8]
    walk_random_forest(rf, crush_row, FEATURE_NAMES)
    walk_xgboost(xgb, crush_row, FEATURE_NAMES)


if __name__ == "__main__":
    demo()
