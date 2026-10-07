#!/usr/bin/env python3
"""
force_model.py

First simple regression check: predict the highest value, over the next `--horizon` ticks, of a link's
  pressure  max_compression_pressure (N)
  held      max_time_over_threshold (s): longest anyone on the link has stayed over
            crushThreshold in a row; crushed at crushDuration (240 s)
from its current state plus previous/next-link context (output of link_context.py).
`--at-horizon` predicts the value at exactly `--horizon` ticks ahead instead (the old target).
Each target gets its own model and its own <out-dir>/<target>/ folder. Whether the
predicted pressure + held time mean "crush" is a separate thresholding step, later.

Reports, for RandomForest and XGBoost against two baselines:
  - MAE and RMSE (N) on held-out data (per fold, and mean +/- std over folds)
  - permutation importance -> which variables matter / can be dropped
  - error by true-force band -> where the model breaks down (the limits)

Splits (rows are never split randomly: the target at tick t comes from the force feature
of the same link's later rows, and neighbouring ticks are near-duplicates,
so a random split would leak the answer):
  default              hold out the last --test-frac of runs (scenario_id); with a
                       single run, the last --test-frac of ticks (debugging only)
  --cv K               K-fold cross-validation grouped by run: every run is tested
                       once, never in train and test at the same time
  --holdout-map MAP    train on the other maps, test on MAP (map_file column)
  --holdout-map all    leave-one-map-out: one fold per map

Usage (on the Linux machine):
    python3 force_model.py crush.duckdb --horizon 10 --cv 5 --out-dir force_model_cv
    python3 force_model.py crush.duckdb --cv 5 --target held        # only the held-time model
    python3 force_model.py crush.duckdb --horizon 10 --holdout-map all --out-dir force_model_maps
    python3 force_model.py crush.duckdb --horizon 10 --cv 5 --observable-only --out-dir force_model_obs
    python3 force_model.py crush.duckdb --horizon 10 --cv 5 --at-horizon --out-dir force_model_at10   # old target
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
TARGETS = {"pressure": (TARGET, "N"), "held": ("max_time_over_threshold", "s")}  # --target name -> (column, unit)
NOT_FEATURES = {"scenario_id", "link_id", TICK, "target", "crush_now", "fold"}  # ids + label-ish
# Simulated forces no camera/sensor could measure in real life. --observable-only drops every
# column whose name contains one of these (current force, push/social force, neighbour forces).
SIM_ONLY = ("force", "pressure", "compression", "time_over_threshold")


def add_target(df, horizon, target=TARGET, window_max=False):
    """target = same link's `target` column `horizon` ticks later; with window_max, its highest
    value over ticks t+1 .. t+horizon (does the link get dangerous at any point in the window).
    A link with no row at a tick had no agents -> 0 (the logger skips empty links)."""
    keys = ["link_id"] + (["scenario_id"] if "scenario_id" in df else [])
    if window_max:
        df = df.reset_index(drop=True)
        out = pd.Series(np.nan, index=df.index)
        for _, g in df.groupby(keys):
            s = g.set_index(TICK)[target]
            full = s.reindex(range(int(s.index.min()), int(s.index.max()) + 1), fill_value=0.0)  # empty ticks = 0
            m = full[::-1].rolling(horizon, min_periods=1).max()[::-1].shift(-1)  # max over t+1 .. t+horizon
            out.loc[g.index] = m.reindex(s.index).to_numpy()
        df = df.assign(target=out)
    else:
        fut = df[keys + [TICK, target]].assign(**{TICK: df[TICK] - horizon}).rename(columns={target: "target"})
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


def run(df, horizon, test_frac, out_dir, observable_only=False, cv=None, holdout_map=None, target=TARGET, unit="N",
        window_max=False, top_features=None, train_runs=None, threshold=None, importance=True):
    print(f"\n######## target: {target} ({unit}), "
          f"{'highest in the next ' + str(horizon) + ' ticks' if window_max else str(horizon) + ' ticks ahead'} ########")
    raw = df  # every tick, including the end of each run that add_target drops (crossing times come from here)
    df = add_target(df, horizon, target, window_max)
    features = [c for c in df.columns if c not in NOT_FEATURES and pd.api.types.is_numeric_dtype(df[c])]
    if observable_only:
        dropped = [c for c in features if any(k in c for k in SIM_ONLY)]
        features = [c for c in features if c not in dropped]
        print(f"observable-only: dropped {len(dropped)} sim-only columns: {', '.join(dropped)}")
    if top_features:
        # ponytail: the ranking usually comes from an earlier run's test folds, so the gain is slightly
        # optimistic; rank inside each fold if a top-K result ever becomes a headline number
        features = [f for f in top_features if f in features]
        print(f"top features only: kept {len(features)}: {', '.join(features)}")
    folds = make_folds(df, test_frac, cv, holdout_map)
    print(f"rows: {len(df):,}; features: {len(features)}; folds: {len(folds)}")

    fold_scores, oof, importances, crush_runs = [], [], [], []
    for i, (name, test) in enumerate(folds):
        train, te = df[~test], df[test]
        if train_runs and "scenario_id" in train:  # learning curve: fewer training runs, same test runs
            # the first N of one fixed shuffle, so a bigger N always contains the smaller N's runs
            keep = np.random.default_rng(i).permutation(train.scenario_id.unique())[:train_runs]
            train = train[train.scenario_id.isin(keep)]
            name += f", trained on {len(keep)} runs"
        if threshold is not None and "scenario_id" in train:
            crush_runs.append(train.loc[train[target] >= threshold, "scenario_id"].nunique())
        print(f"-- {name}: train {len(train):,} rows, test {len(te):,} rows; "
              f"test target mean {te.target.mean():.1f} {unit}, max {te.target.max():.1f} {unit}")
        preds = {
            "baseline: train mean": np.full(len(te), train.target.mean()),
            "baseline: now": te[target].to_numpy(),  # "nothing changes" -- the model must beat this
        }
        models = new_models()
        for m_name, m in models.items():
            m.fit(train[features], train.target)
            preds[m_name] = m.predict(te[features])
        for k, p in preds.items():
            fold_scores.append({"fold": name, "model": k, **scores(te.target, p)})
        if importance:
            best = min(models, key=lambda k: mean_absolute_error(te.target, preds[k]))
            imp = permutation_importance(models[best], te[features], te.target, scoring="neg_mean_absolute_error",
                                         n_repeats=3, random_state=0, n_jobs=1)  # worker processes each get a copy of the forest; even 4 filled /dev/shm
            importances.append(pd.Series(imp.importances_mean, index=features, name=name))
        keep = [c for c in ["scenario_id", "map_file", "link_id", TICK] if c in te]
        oof.append(te[keep].assign(fold=name, true=te.target.to_numpy(),
                                   **{f"pred_{k}": v for k, v in preds.items()}))

    per_fold = pd.DataFrame(fold_scores)
    summary = per_fold.groupby("model", sort=False)[["MAE", "RMSE"]].agg(["mean", "std"])
    print(f"\n== accuracy over folds (lower is better, {unit}; std = spread across folds) ==")
    print(summary.round(2).to_string())
    if len(folds) > 1:
        print("\n== MAE per fold ==")
        print(per_fold.pivot(index="fold", columns="model", values="MAE").round(2).to_string())

    if importance:
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
    print(f"\n== {best} error by true-target band ({unit}), all held-out rows (negative bias = under-predicts) ==")
    print(by_band.round(2).to_string())

    out_dir.mkdir(parents=True, exist_ok=True)
    if threshold is not None:
        # Crossings: below the threshold now, at or above it in the target. The no-change baseline
        # can never predict one, so anything caught here is something the model learned.
        calm = oof["pred_baseline: now"] < threshold
        onset = calm & (y >= threshold)
        calls = {m: calm & (oof[f"pred_{m}"] >= threshold) for m in new_models()}
        cross = pd.DataFrame({"crossings": onset.sum(), "caught": {m: (c & onset).sum() for m, c in calls.items()},
                              "alarms": {m: c.sum() for m, c in calls.items()}})
        cross["caught_share"] = cross.caught / cross.crossings
        cross["alarms_real_share"] = cross.caught / cross.alarms
        runs = f" from {oof.scenario_id[onset].nunique()} runs" if "scenario_id" in oof else ""
        # Events: one per (run, link), the first time it reaches the threshold. Warned = the model
        # raised an alarm on that link in the `horizon` ticks before it; warning = how early the first one came.
        ev = first_crossings(raw, target, threshold)
        keys = list(ev.index.names)
        o = oof.join(ev, on=keys, how="inner")
        o = o.assign(lead=o.cross_tick - o[TICK])
        o = o[(o.lead > 0) & (o.lead <= horizon)]
        cross["events"] = o.groupby(keys).ngroups
        for m in cross.index:
            lead = o[o[f"pred_{m}"] >= threshold].groupby(keys).lead.max()
            cross.loc[m, "events_warned"], cross.loc[m, "median_warning_s"] = len(lead), lead.median()
        cross["warned_share"] = cross.events_warned / cross.events
        print(f"\n== crossings of {threshold:g} {unit}: below it now, at or above it in the target{runs} ==")
        print(cross.round(3).to_string())
        cross.to_csv(out_dir / "crossings.csv")
        summary.attrs.update(crossings=cross.to_dict("index"), crush_runs=float(np.mean(crush_runs)) if crush_runs else np.nan)
    summary.to_csv(out_dir / "accuracy.csv")
    per_fold.to_csv(out_dir / "accuracy_per_fold.csv", index=False)
    if importance:
        imp.to_csv(out_dir / "importance.csv", index=False)
    by_band.to_csv(out_dir / "error_by_band.csv")
    oof.to_csv(out_dir / "predictions.csv", index=False)
    print(f"\nwrote {out_dir}/accuracy.csv, accuracy_per_fold.csv, importance.csv, error_by_band.csv, predictions.csv")
    return summary, oof


def first_crossings(df, target, threshold):
    """Tick at which each (run, link) first reaches the threshold, for links that were below it before
    (so a warning was possible). ponytail: only the first crossing per link per run; count
    re-crossings too if links turn out to cross, clear and cross again often."""
    keys = [k for k in ("scenario_id", "link_id") if k in df]
    hot = df[df[target] >= threshold].groupby(keys)[TICK].min().rename("cross_tick")
    first = df.groupby(keys)[TICK].min().rename("first_tick")
    ev = pd.concat([hot, first], axis=1).dropna()
    return ev.cross_tick[ev.cross_tick > ev.first_tick]


def load_data(path):
    """All runs from a DuckDB file filled by link_context.py (table link_ticks), or a CSV."""
    if path.suffix == ".csv":
        return pd.read_csv(path)
    import duckdb
    from link_context import TABLE
    with duckdb.connect(str(path), read_only=True) as con:
        return con.table(TABLE).df()


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
        assert mae(s, m) < mae(s, "baseline: now") and mae(s, m) < mae(s, "baseline: train mean"), m

    # --cv 3: every run tested exactly once, never in train and test of the same fold -- but a MAP
    # can be in both (other runs on it), which is the "known layout, new crowd" test
    s, oof = run(df, horizon=5, test_frac=0.2, out_dir=tmp / "cv", cv=3)
    assert oof.groupby("scenario_id").fold.nunique().eq(1).all() and oof.scenario_id.nunique() == 6
    assert oof.fold.nunique() == 3 and mae(s, "RandomForest") < mae(s, "baseline: now")
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

    # held-time target: its own column is the target, and observable-only hides it from the features
    held = df.assign(max_time_over_threshold=(df[TARGET] > 500).groupby(df.scenario_id).cumsum().astype(float))
    assert add_target(held, 5, "max_time_over_threshold").target[0] == held.max_time_over_threshold[5]
    run(held, horizon=5, test_frac=0.2, out_dir=tmp / "held", cv=3, observable_only=True,
        target="max_time_over_threshold", unit="s")
    assert "max_time_over_threshold" not in set(pd.read_csv(tmp / "held" / "importance.csv").feature)

    # --window-max: target = highest value over the next `horizon` ticks, empty ticks count as 0
    assert add_target(df, 5, window_max=True).target[0] == df[TARGET][1:6].max()
    gap = pd.DataFrame({"scenario_id": "g", "link_id": "b", TICK: [0, 1, 2, 8, 20], TARGET: [5.0, 9.0, 1.0, 3.0, 0.0]})
    assert list(add_target(gap, 3, window_max=True).target) == [9.0, 1.0, 0.0, 0.0]  # tick 8 sees only empty ticks
    s, _ = run(df, horizon=5, test_frac=0.2, out_dir=tmp / "wmax", cv=3, window_max=True)
    assert mae(s, "RandomForest") < mae(s, "baseline: now")

    # the three experiment switches: top features only, fewer training runs, crossings table, no importance
    run(df, horizon=5, test_frac=0.2, out_dir=tmp / "exp", cv=3, top_features=["prev_inflow", "not_a_column"],
        train_runs=2, threshold=500, importance=False)
    cross = pd.read_csv(tmp / "exp" / "crossings.csv", index_col=0)
    assert (cross.caught <= cross.crossings).all() and cross.crossings.iloc[0] > 0
    assert not (tmp / "exp" / "importance.csv").exists()
    assert (cross.events_warned <= cross.events).all() and cross.events.iloc[0] > 0

    # event timing: link crosses 500 at tick 3; an alarm at tick 1 is a 2-tick warning
    ev = pd.DataFrame({"scenario_id": "e", "link_id": "b", TICK: range(6), TARGET: [0.0, 100, 200, 600, 700, 0]})
    assert first_crossings(ev, TARGET, 500).iloc[0] == 3
    assert first_crossings(ev.assign(**{TARGET: 900.0}), TARGET, 500).empty  # never below it: no warning possible
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Predict link pressure and held time ahead; report MAE/RMSE.")
    p.add_argument("data", type=Path, help="DuckDB file filled by link_context.py (all runs in link_ticks), or a CSV")
    p.add_argument("--horizon", type=int, default=10, help="ticks ahead to predict (default 10)")
    split = p.add_mutually_exclusive_group()
    split.add_argument("--cv", type=int, default=None, metavar="K", help="K-fold cross-validation grouped by run")
    split.add_argument("--holdout-map", default=None, metavar="MAP",
                       help="test on this map_file, train on the others; 'all' = leave-one-map-out")
    p.add_argument("--test-frac", type=float, default=0.2, help="default split only")
    p.add_argument("--target", nargs="+", choices=TARGETS, default=list(TARGETS),
                   help="what to predict: pressure (N) and/or held (s above crushThreshold); default both")
    p.add_argument("--out-dir", type=Path, default=Path("force_model_out"), help="results go in <out-dir>/<target>/")
    p.add_argument("--observable-only", action="store_true",
                   help="drop simulated force/pressure columns (not measurable in real life) from the features")
    p.add_argument("--at-horizon", action="store_true",
                   help="predict the value at exactly --horizon ticks ahead (the old target), "
                        "not the highest value over the next --horizon ticks")
    p.add_argument("--top-features", type=int, default=None, metavar="K",
                   help="train on only the K strongest features listed in --features-from")
    p.add_argument("--features-from", type=Path, default=None, metavar="IMPORTANCE_CSV",
                   help="importance.csv of an earlier run (strongest feature first)")
    p.add_argument("--train-runs", type=int, default=None, metavar="N",
                   help="learning curve: train each fold on only N of its training runs (test runs unchanged)")
    p.add_argument("--threshold", type=float, default=1062.0,
                   help="pressure (N) that counts as a crossing in crossings.csv (default 1062 = crushThreshold - margin)")
    p.add_argument("--no-importance", action="store_true", help="skip permutation importance (the slow part)")
    args = p.parse_args()
    top = None
    if args.top_features:
        if not args.features_from:
            p.error("--top-features needs --features-from <importance.csv>")
        top = list(pd.read_csv(args.features_from).feature[:args.top_features])
    df = load_data(args.data)
    for name in args.target:
        col, unit = TARGETS[name]
        if col not in df:
            print(f"\n[!] skipping target {name}: no {col} column (add time_over_threshold to link_logging.fields)")
            continue
        run(df, args.horizon, args.test_frac, args.out_dir / name, args.observable_only, args.cv,
            args.holdout_map, col, unit, window_max=not args.at_horizon, top_features=top,
            train_runs=args.train_runs, threshold=args.threshold if name == "pressure" else None,
            importance=not args.no_importance)


if __name__ == "__main__":
    main()
