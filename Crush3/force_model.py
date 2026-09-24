#!/usr/bin/env python3
"""
force_model.py

First simple regression check: predict a link's max_compression_pressure
`--horizon` ticks ahead from its current state plus previous/next-link context
(output of link_context.py). Whether that predicted force means "crush" is a
separate thresholding step, later.

Reports, for RandomForest and XGBoost against two baselines:
  - MAE and RMSE (N) on held-out data (per fold, and mean +/- std over folds)
  - permutation importance -> which variables matter / can be dropped
  - error by true-force band -> where the model breaks down (the limits)

Splits (rows are never split randomly: the target at tick t is the force feature
of the same link's row at t+horizon, and neighbouring ticks are near-duplicates,
so a random split would leak the answer):
  default              hold out the last --test-frac of runs (scenario_id); with a
                       single run, the last --test-frac of ticks (debugging only)
  --cv K               K-fold cross-validation grouped by run: every run is tested
                       once, never in train and test at the same time
  --holdout-map MAP    train on the other maps, test on MAP (map_file column)
  --holdout-map all    leave-one-map-out: one fold per map

Usage (on the Linux machine):
    python3 force_model.py crush.duckdb --horizon 10 --cv 5 --out-dir force_model_cv
    python3 force_model.py crush.duckdb --horizon 10 --holdout-map all --out-dir force_model_maps
    python3 force_model.py crush.duckdb --horizon 10 --cv 5 --observable-only --out-dir force_model_obs
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
from sklearn.model_selection import GroupKFold
from xgboost import XGBRegressor

TICK = "current_traveling_period"
TARGET = "max_compression_pressure"
NOT_FEATURES = {"scenario_id", "link_id", TICK, "target", "crush_now", "fold"}  # ids + label-ish
# Simulated forces no camera/sensor could measure in real life. --observable-only drops every
# column whose name contains one of these (current force, push/social force, neighbour forces).
SIM_ONLY = ("force", "pressure", "compression", "time_over_threshold")


def add_target(df, horizon):
    """target = same link's max_compression_pressure `horizon` ticks later.
    A link with no row at t+h had no agents -> 0 N (the logger skips empty links)."""
    keys = ["link_id"] + (["scenario_id"] if "scenario_id" in df else [])
    fut = df[keys + [TICK, TARGET]].assign(**{TICK: df[TICK] - horizon}).rename(columns={TARGET: "target"})
    df = df.merge(fut, on=keys + [TICK], how="left")
    last_tick = df.groupby("scenario_id")[TICK].transform("max") if "scenario_id" in df else df[TICK].max()
    df = df[df[TICK] + horizon <= last_tick]  # future unknown past the end of the run
    return df.assign(target=df.target.fillna(0.0)).reset_index(drop=True)


def make_folds(df, test_frac=0.2, cv=None, holdout_map=None):
    """List of (name, test_mask) -- train is always everything not in test."""
    if holdout_map:
        # Group by map_id (hash of the map's contents) so every run on the same map is held out
        # together, even if the files sit in different folders; map_file is only a readable label.
        key = "map_id" if "map_id" in df else "map_file"
        if key not in df:
            sys.exit("--holdout-map needs map_id/map_file columns (load runs with link_context.py)")
        labels = df.groupby(key).map_file.first() if "map_file" in df else pd.Series(df[key].unique(), df[key].unique())
        if len(labels) < 2:
            sys.exit(f"--holdout-map needs runs on at least 2 maps; only have {list(labels)}")
        if holdout_map == "all":
            chosen = list(labels.index)
        else:
            chosen = [k for k, lab in labels.items() if holdout_map in (k, lab)]
            if not chosen:
                sys.exit(f"map {holdout_map!r} not in data; maps are {sorted(labels)}")
        return [(f"test map {labels[k]} ({df[key].eq(k).groupby(df.scenario_id).any().sum()} runs)"
                 if "scenario_id" in df else f"test map {labels[k]}", (df[key] == k).to_numpy()) for k in chosen]
    if cv:
        runs = df.scenario_id.nunique() if "scenario_id" in df else 1
        if runs < cv:
            sys.exit(f"--cv {cv} needs at least {cv} runs (scenario_ids); have {runs}")
        folds = []
        for i, (_, te) in enumerate(GroupKFold(n_splits=cv).split(df, groups=df.scenario_id)):
            mask = np.zeros(len(df), bool)
            mask[te] = True
            folds.append((f"fold {i + 1} ({df.scenario_id[mask].nunique()} runs)", mask))
        return folds
    if "scenario_id" in df and df.scenario_id.nunique() > 1:
        ids = sorted(df.scenario_id.unique())
        test_ids = ids[-max(1, round(len(ids) * test_frac)):]
        return [(f"held-out runs {test_ids}", df.scenario_id.isin(test_ids).to_numpy())]
    cut = df[TICK].quantile(1 - test_frac)
    return [(f"single run: test ticks > {cut:.0f} (debugging only)", (df[TICK] > cut).to_numpy())]


def scores(y, pred):
    return {"MAE": mean_absolute_error(y, pred), "RMSE": mean_squared_error(y, pred) ** 0.5}


def new_models():
    return {
        "RandomForest": RandomForestRegressor(n_estimators=300, min_samples_leaf=5, n_jobs=-1, random_state=0),
        "XGBoost": XGBRegressor(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.8, random_state=0),
    }


def run(df, horizon, test_frac, out_dir, observable_only=False, cv=None, holdout_map=None):
    df = add_target(df, horizon)
    features = [c for c in df.columns if c not in NOT_FEATURES and pd.api.types.is_numeric_dtype(df[c])]
    if observable_only:
        dropped = [c for c in features if any(k in c for k in SIM_ONLY)]
        features = [c for c in features if c not in dropped]
        print(f"observable-only: dropped {len(dropped)} sim-only columns: {', '.join(dropped)}")
    folds = make_folds(df, test_frac, cv, holdout_map)
    print(f"rows: {len(df):,}; features: {len(features)}; folds: {len(folds)}")

    fold_scores, oof, importances = [], [], []
    for name, test in folds:
        train, te = df[~test], df[test]
        print(f"-- {name}: train {len(train):,} rows, test {len(te):,} rows; "
              f"test target mean {te.target.mean():.1f} N, max {te.target.max():.1f} N")
        preds = {
            "baseline: train mean": np.full(len(te), train.target.mean()),
            "baseline: force now": te[TARGET].to_numpy(),  # "nothing changes" -- the model must beat this
        }
        models = new_models()
        for m_name, m in models.items():
            m.fit(train[features], train.target)
            preds[m_name] = m.predict(te[features])
        for k, p in preds.items():
            fold_scores.append({"fold": name, "model": k, **scores(te.target, p)})
        best = min(models, key=lambda k: mean_absolute_error(te.target, preds[k]))
        imp = permutation_importance(models[best], te[features], te.target, scoring="neg_mean_absolute_error",
                                     n_repeats=3, random_state=0, n_jobs=-1)
        importances.append(pd.Series(imp.importances_mean, index=features, name=name))
        keep = [c for c in ["scenario_id", "map_file", "link_id", TICK] if c in te]
        oof.append(te[keep].assign(fold=name, true=te.target.to_numpy(),
                                   **{f"pred_{k}": v for k, v in preds.items()}))

    per_fold = pd.DataFrame(fold_scores)
    summary = per_fold.groupby("model", sort=False)[["MAE", "RMSE"]].agg(["mean", "std"])
    print("\n== accuracy over folds (lower is better, N; std = spread across folds) ==")
    print(summary.round(2).to_string())
    if len(folds) > 1:
        print("\n== MAE per fold ==")
        print(per_fold.pivot(index="fold", columns="model", values="MAE").round(2).to_string())

    imp = pd.concat(importances, axis=1)
    imp = pd.DataFrame({"MAE_increase_when_shuffled": imp.mean(axis=1), "std_across_folds": imp.std(axis=1)})
    imp = imp.sort_values("MAE_increase_when_shuffled", ascending=False).rename_axis("feature").reset_index()
    print("\n== permutation importance (best model per fold, averaged) -- ~0 or negative = candidate to drop ==")
    print(imp.round(3).to_string(index=False))

    # Limits: error by true-force band, pooled over every fold's held-out rows.
    oof = pd.concat(oof, ignore_index=True)
    best = summary[("MAE", "mean")].loc[list(new_models())].idxmin()
    y = oof.true
    bands = pd.cut(y, [-np.inf, 0, y.quantile(0.5), y.quantile(0.9), y.quantile(0.99), np.inf], duplicates="drop")
    err = pd.DataFrame({"band": bands, "abs_err": (oof[f"pred_{best}"] - y).abs(), "bias": oof[f"pred_{best}"] - y})
    by_band = err.groupby("band", observed=True).agg(rows=("abs_err", "size"), MAE=("abs_err", "mean"),
                                                      mean_bias=("bias", "mean"))
    print(f"\n== {best} error by true-force band, all held-out rows (negative bias = under-predicts) ==")
    print(by_band.round(2).to_string())

    out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_dir / "accuracy.csv")
    per_fold.to_csv(out_dir / "accuracy_per_fold.csv", index=False)
    imp.to_csv(out_dir / "importance.csv", index=False)
    by_band.to_csv(out_dir / "error_by_band.csv")
    oof.to_csv(out_dir / "predictions.csv", index=False)
    print(f"\nwrote {out_dir}/accuracy.csv, accuracy_per_fold.csv, importance.csv, error_by_band.csv, predictions.csv")
    return summary, oof


def selftest():
    # force on a link at t+h is driven by upstream inflow at t; the model should beat both baselines
    rng = np.random.default_rng(0)

    def one_run(sid, map_file, T=300):
        inflow = np.clip(np.sin(np.arange(T) / 25 + rng.uniform(0, 6)) * 3 + 3 + rng.normal(0, 0.3, T), 0, None)
        force = np.r_[np.zeros(5), inflow[:-5] * 200] + rng.normal(0, 20, T)
        return pd.DataFrame({"scenario_id": sid, "map_file": map_file, TICK: np.arange(T), "link_id": "b",
                             "agent_count": 10, "prev_inflow": inflow, TARGET: force})

    df = pd.concat([one_run(f"r{i}", "m1.xml" if i < 4 else "m2.xml") for i in range(6)], ignore_index=True)
    tmp = Path(tempfile.mkdtemp())
    mae = lambda s, m: s.loc[m, ("MAE", "mean")]

    s, _ = run(df, horizon=5, test_frac=0.2, out_dir=tmp / "default")
    for m in ("RandomForest", "XGBoost"):
        assert mae(s, m) < mae(s, "baseline: force now") and mae(s, m) < mae(s, "baseline: train mean"), m

    # --cv 3: every run tested exactly once, never in train and test of the same fold -- but a MAP
    # can be in both (other runs on it), which is the "known layout, new crowd" test
    s, oof = run(df, horizon=5, test_frac=0.2, out_dir=tmp / "cv", cv=3)
    assert oof.groupby("scenario_id").fold.nunique().eq(1).all() and oof.scenario_id.nunique() == 6
    assert oof.fold.nunique() == 3 and mae(s, "RandomForest") < mae(s, "baseline: force now")
    d = add_target(df, 5)
    assert any(set(d.map_file[m]) & set(d.map_file[~m]) for _, m in make_folds(d, cv=3))

    # map_id wins over map_file: same map under two labels is still held out as one map
    d2 = df.assign(map_id=df.map_file.map({"m1.xml": "aaa", "m2.xml": "bbb"}))
    d2.loc[d2.scenario_id == "r0", "map_file"] = "copy/m1.xml"
    folds = make_folds(add_target(d2, 5), holdout_map="copy/m1.xml")
    assert len(folds) == 1 and "(4 runs)" in folds[0][0]

    # --holdout-map: test rows come only from the held-out map
    _, oof = run(df, horizon=5, test_frac=0.2, out_dir=tmp / "map", holdout_map="m2.xml")
    assert set(oof.map_file) == {"m2.xml"} and oof.scenario_id.nunique() == 2
    _, oof = run(df, horizon=5, test_frac=0.2, out_dir=tmp / "maps", holdout_map="all")
    assert oof.groupby("map_file").fold.nunique().eq(1).all() and oof.fold.nunique() == 2

    # observable-only: force columns out of the features
    run(df.assign(max_push_force=1.0, prev_max_compression_pressure=2.0), horizon=5, test_frac=0.2,
        out_dir=tmp / "obs", cv=3, observable_only=True)
    assert set(pd.read_csv(tmp / "obs" / "importance.csv").feature) == {"prev_inflow", "agent_count"}
    assert add_target(df, 5).set_index([TICK, "scenario_id"]).target[(0, "r0")] == df[TARGET][5]
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Predict max_compression_pressure ahead; report MAE/RMSE.")
    p.add_argument("data", type=Path, help="DuckDB file filled by link_context.py (all runs in link_ticks), or a CSV")
    p.add_argument("--horizon", type=int, default=10, help="ticks ahead to predict (default 10)")
    split = p.add_mutually_exclusive_group()
    split.add_argument("--cv", type=int, default=None, metavar="K", help="K-fold cross-validation grouped by run")
    split.add_argument("--holdout-map", default=None, metavar="MAP",
                       help="test on this map_file, train on the others; 'all' = leave-one-map-out")
    p.add_argument("--test-frac", type=float, default=0.2, help="default split only")
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
            df = con.table(TABLE).df()
    run(df, args.horizon, args.test_frac, args.out_dir, args.observable_only, args.cv, args.holdout_map)


if __name__ == "__main__":
    main()
