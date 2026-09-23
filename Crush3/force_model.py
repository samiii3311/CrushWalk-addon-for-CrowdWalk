#!/usr/bin/env python3
"""
force_model.py

First simple regression check: predict a link's max_compression_pressure
`--horizon` ticks ahead from its current state plus previous/next-link context
(output of link_context.py). Whether that predicted force means "crush" is a
separate thresholding step, later.

Reports, for RandomForest and XGBoost against two baselines:
  - MAE and RMSE (N) on held-out data
  - permutation importance -> which variables matter / can be dropped
  - error by true-force band -> where the model breaks down (the limits)

Split: if the CSV has several scenario_ids, whole scenarios are held out;
otherwise the last --test-frac of ticks is held out. Rows are never split
randomly, since neighbouring ticks are near-duplicates and would leak.

Usage (on the Linux machine):
    python3 force_model.py crush.duckdb --horizon 10 --out-dir force_model_out
    python3 force_model.py crush.duckdb --horizon 10 --observable-only --out-dir force_model_obs
    python3 force_model.py --selftest
"""

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, mean_squared_error
from xgboost import XGBRegressor

TICK = "current_traveling_period"
TARGET = "max_compression_pressure"
NOT_FEATURES = {"scenario_id", "link_id", TICK, "target", "crush_now"}  # ids + label-ish
# Simulated forces no camera/sensor could measure in real life. --observable-only drops every
# column whose name contains one of these (current force, push/social force, neighbour forces).
SIM_ONLY = ("force", "pressure", "compression")


def add_target(df, horizon):
    """target = same link's max_compression_pressure `horizon` ticks later.
    A link with no row at t+h had no agents -> 0 N (the logger skips empty links)."""
    keys = ["link_id"] + (["scenario_id"] if "scenario_id" in df else [])
    fut = df[keys + [TICK, TARGET]].assign(**{TICK: df[TICK] - horizon}).rename(columns={TARGET: "target"})
    df = df.merge(fut, on=keys + [TICK], how="left")
    last_tick = df.groupby("scenario_id")[TICK].transform("max") if "scenario_id" in df else df[TICK].max()
    df = df[df[TICK] + horizon <= last_tick]  # future unknown past the end of the run
    return df.assign(target=df.target.fillna(0.0))


def split(df, test_frac):
    if "scenario_id" in df and df.scenario_id.nunique() > 1:
        ids = sorted(df.scenario_id.unique())
        test_ids = ids[-max(1, round(len(ids) * test_frac)):]
        test = df.scenario_id.isin(test_ids)
        print(f"split: held-out scenarios {test_ids}")
    else:
        cut = df[TICK].quantile(1 - test_frac)
        test = df[TICK] > cut
        print(f"split: train ticks <= {cut:.0f}, test ticks > {cut:.0f}")
    return df[~test], df[test]


def scores(y, pred):
    return {"MAE": mean_absolute_error(y, pred), "RMSE": mean_squared_error(y, pred) ** 0.5}


def run(df, horizon, test_frac, out_dir, observable_only=False):
    df = add_target(df, horizon)
    features = [c for c in df.columns if c not in NOT_FEATURES and pd.api.types.is_numeric_dtype(df[c])]
    if observable_only:
        dropped = [c for c in features if any(k in c for k in SIM_ONLY)]
        features = [c for c in features if c not in dropped]
        print(f"observable-only: dropped {len(dropped)} sim-only columns: {', '.join(dropped)}")
    train, test = split(df, test_frac)
    Xtr, ytr, Xte, yte = train[features], train.target, test[features], test.target
    print(f"rows: train {len(train):,}, test {len(test):,}; features: {len(features)}")
    print(f"target on test: mean {yte.mean():.1f} N, max {yte.max():.1f} N, "
          f"{(yte > 0).mean():.1%} of rows > 0\n")

    models = {
        "RandomForest": RandomForestRegressor(n_estimators=300, min_samples_leaf=5, n_jobs=-1, random_state=0),
        "XGBoost": XGBRegressor(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.8, random_state=0),
    }
    preds = {
        "baseline: train mean": np.full(len(yte), ytr.mean()),
        "baseline: force now": test[TARGET].to_numpy(),  # "nothing changes" -- the model must beat this
    }
    for name, m in models.items():
        m.fit(Xtr, ytr)
        preds[name] = m.predict(Xte)

    results = pd.DataFrame({k: scores(yte, p) for k, p in preds.items()}).T
    print("== accuracy (lower is better, N) ==")
    print(results.round(2).to_string(), "\n")

    best = results.loc[list(models)].MAE.idxmin()
    imp = permutation_importance(models[best], Xte, yte, scoring="neg_mean_absolute_error",
                                 n_repeats=5, random_state=0, n_jobs=-1)
    imp = pd.DataFrame({"feature": features, "MAE_increase_when_shuffled": imp.importances_mean,
                        "std": imp.importances_std}).sort_values("MAE_increase_when_shuffled", ascending=False)
    print(f"== permutation importance ({best}) -- ~0 or negative = candidate to drop ==")
    print(imp.round(3).to_string(index=False), "\n")

    # Limits: error by true-force band. High-force rows are the ones that matter for crush.
    bands = pd.cut(yte, [-np.inf, 0, yte.quantile(0.5), yte.quantile(0.9), yte.quantile(0.99), np.inf],
                   duplicates="drop")
    err = pd.DataFrame({"band": bands, "abs_err": np.abs(preds[best] - yte), "bias": preds[best] - yte})
    by_band = err.groupby("band", observed=True).agg(rows=("abs_err", "size"), MAE=("abs_err", "mean"),
                                                      mean_bias=("bias", "mean"))
    print(f"== {best} error by true-force band (negative bias = under-predicts) ==")
    print(by_band.round(2).to_string())

    out_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_dir / "accuracy.csv")
    imp.to_csv(out_dir / "importance.csv", index=False)
    by_band.to_csv(out_dir / "error_by_band.csv")
    keep = [c for c in ["scenario_id", "link_id", TICK] if c in test]
    test[keep].assign(true=yte, **{f"pred_{k}": v for k, v in preds.items()}).to_csv(
        out_dir / "predictions.csv", index=False)
    print(f"\nwrote {out_dir}/accuracy.csv, importance.csv, error_by_band.csv, predictions.csv")
    return results


def selftest():
    # force on link b at t+h is driven by upstream inflow at t; the model should beat both baselines
    rng = np.random.default_rng(0)
    T = 600
    inflow = np.clip(np.sin(np.arange(T) / 25) * 3 + 3 + rng.normal(0, 0.3, T), 0, None)
    force = np.r_[np.zeros(5), inflow[:-5] * 200] + rng.normal(0, 20, T)
    df = pd.DataFrame({TICK: np.arange(T), "link_id": "b", "agent_count": 10, "prev_inflow": inflow,
                       TARGET: force})
    r = run(df, horizon=5, test_frac=0.2, out_dir=Path(tempfile.mkdtemp()))
    for m in ("RandomForest", "XGBoost"):
        assert r.loc[m, "MAE"] < r.loc["baseline: force now", "MAE"], m
        assert r.loc[m, "MAE"] < r.loc["baseline: train mean", "MAE"], m
    # observable-only: force columns out of the features, prev_inflow alone still predicts
    out = Path(tempfile.mkdtemp())
    r2 = run(df.assign(max_push_force=1.0, prev_max_compression_pressure=2.0), horizon=5, test_frac=0.2,
             out_dir=out, observable_only=True)
    assert r2.loc["RandomForest", "MAE"] < r2.loc["baseline: force now", "MAE"]
    assert set(pd.read_csv(out / "importance.csv").feature) == {"prev_inflow", "agent_count"}
    assert add_target(df, 5).set_index(TICK).target[0] == df[TARGET][5]
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Predict max_compression_pressure ahead; report MAE/RMSE.")
    p.add_argument("data", type=Path, help="DuckDB file filled by link_context.py (all runs in link_ticks), or a CSV")
    p.add_argument("--horizon", type=int, default=10, help="ticks ahead to predict (default 10)")
    p.add_argument("--test-frac", type=float, default=0.2)
    p.add_argument("--out-dir", type=Path, default=Path("force_model_out"))
    p.add_argument("--observable-only", action="store_true",
                   help="drop simulated force/pressure columns (not measurable in real life) from the features")
    args = p.parse_args()
    if args.data.suffix == ".csv":
        df = pd.read_csv(args.data)
    else:
        import duckdb
        from link_context import TABLE
        with duckdb.connect(str(args.data), read_only=True) as con:
            df = con.execute(f"SELECT * FROM {TABLE}").df()
    run(df, args.horizon, args.test_frac, args.out_dir, args.observable_only)


if __name__ == "__main__":
    main()
