#!/usr/bin/env python3
"""
extra_models.py

Side experiment: do other models change the warning time? force_model.py and sweep.py are
not touched; this script swaps the model list and reuses their folds, target and event scoring,
so the numbers line up with an existing sweep's results.csv.

Two passes over every window, each trained on all runs:

  plain     same features as force_model.py
              Ridge    linear regression (median-filled, scaled). A floor: if it warns as early
                       as the trees, the signal is simple.
              Formula  no learning from the feature table: crowd pressure = density x speed
                       variance (Helbing's measure, the risk proxy in Luerig's continuum model),
                       here density * std_speed^2 per link. One monotone curve fitted on the
                       training runs turns it into newtons so it can raise the same 1062 N alarm.
  history   the same features plus, for each column in LAG_COLS, its change over the last
            10, 30 and 60 ticks (<column>_d10 ...). Built per run and link from ticks <= t only.
              RF_history, XGB_history, Ridge_history
            If these warn no earlier than the plain models, a sequence model (LSTM/TCN) has
            nothing extra to learn from and is not worth building.

Writes <out-dir>/plain/ and <out-dir>/history/ (one sweep.py-style folder each) and
<out-dir>/results.csv with every model, which sweep_plot.py can read.

Usage (on the Linux machine):
    python3 extra_models.py crush.duckdb --baseline fm_sweep_1way/results.csv --out-dir fm_extra
    python3 extra_models.py crush.duckdb --windows 120,300,600 --only plain --out-dir fm_extra
    python3 extra_models.py --selftest
"""

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

import force_model
from force_model import TARGET, TICK, load_data
from sweep import sweep

force_model_new_models = force_model.new_models  # the RF + XGBoost list, put back after the passes
LAGS = (10, 30, 60)  # ticks back
# only columns where "no row at that tick" really means 0 (the logger skips empty links)
LAG_COLS = ("density", "agent_count", TARGET, "prev_inflow", "prev_agent_count", "next_density",
            "prev_max_compression_pressure", "next_max_compression_pressure")


def crowd_pressure(X):
    return (X["density"] * X["std_speed"].fillna(0.0) ** 2).to_numpy().reshape(-1, 1)


def ridge():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge())


def plain_models(formula=True):
    models = {"Ridge": ridge()}
    if formula:
        models["Formula"] = make_pipeline(FunctionTransformer(crowd_pressure), IsotonicRegression(out_of_bounds="clip"))
    return models


def history_models():
    trees = force_model_new_models()
    return {"RF_history": trees["RandomForest"], "XGB_history": trees["XGBoost"], "Ridge_history": ridge()}


def add_lags(df, lags=LAGS):
    """<col>_d<L> = col now minus col L ticks ago, per (run, link). A link with no row L ticks ago was empty -> 0."""
    keys = [k for k in ("scenario_id", "link_id") if k in df]
    cols = [c for c in LAG_COLS if c in df]
    n = len(df)
    for lag in lags:
        names = {c: f"{c}_d{lag}" for c in cols}
        past = df[keys + [TICK] + cols].assign(**{TICK: df[TICK] + lag}).rename(columns=names)
        df = df.merge(past, on=keys + [TICK], how="left")
        for c, name in names.items():
            df[name] = df[c] - df[name].fillna(0.0)
    assert len(df) == n, "more than one row per (run, link, tick)"
    return df


def run_passes(df, windows, out_dir, cv=5, threshold=1062.0, only=None):
    formula = {"density", "std_speed"} <= set(df)
    if not formula:
        print("[!] no density/std_speed columns: skipping Formula")
    passes = {"plain": (lambda: plain_models(formula), lambda d: d), "history": (history_models, add_lags)}
    res = []
    for name, (models, prepare) in passes.items():
        if only in (None, name):
            force_model.new_models = models  # run() looks the model list up at call time
            res.append(sweep(prepare(df), windows, [0], out_dir / name, cv, threshold))
    force_model.new_models = force_model_new_models
    return pd.concat(res, ignore_index=True)


def show(res):
    for col, title in [("median_warning_s", "typical warning before an event (s)"),
                       ("alarms_real_share", "share of alarms that were real"),
                       ("warned_share", "share of events warned")]:
        print(f"\n== {title}; columns = window (s) ==")
        print(res.pivot_table(index="model", columns="window_s", values=col, sort=False).round(2).to_string())


def selftest():
    lag = pd.DataFrame({"scenario_id": "g", "link_id": "b", TICK: [0, 1, 2, 12], "density": [1.0, 3.0, 4.0, 9.0]})
    assert list(add_lags(lag, (1, 10)).density_d1) == [1.0, 2.0, 1.0, 9.0]   # tick 11 had no row: empty link
    assert list(add_lags(lag, (1, 10)).density_d10) == [1.0, 3.0, 4.0, 5.0]  # tick 12 minus tick 2

    rng = np.random.default_rng(0)

    def one_run(sid, T=300):  # sweep.py's toy data, plus the two columns the formula needs
        inflow = np.clip(np.sin(np.arange(T) / 25 + rng.uniform(0, 6)) * 3 + 3 + rng.normal(0, 0.3, T), 0, None)
        force = np.r_[np.zeros(5), inflow[:-5] * 200] + rng.normal(0, 20, T)
        return pd.DataFrame({"scenario_id": sid, TICK: np.arange(T), "link_id": "b", "prev_inflow": inflow,
                             "density": inflow / 2, "std_speed": rng.uniform(0, 1, T), TARGET: force})

    df = pd.concat([one_run(f"r{i}") for i in range(6)], ignore_index=True)
    res = run_passes(df, [5, 10], Path(tempfile.mkdtemp()), cv=3, threshold=500)
    assert set(res.model) == {"Ridge", "Formula", "RF_history", "XGB_history", "Ridge_history"} and len(res) == 10
    assert res.warned_share.between(0, 1).all()
    assert set(force_model.new_models()) == {"RandomForest", "XGBoost"}  # model list put back
    show(res)
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Ridge, a density x speed-variance formula and history features, "
                                            "scored like sweep.py.")
    p.add_argument("data", type=Path, help="DuckDB file filled by link_context.py, or a CSV")
    p.add_argument("--windows", default="30,60,120,300,600", help="window lengths in ticks (default 30,60,120,300,600)")
    p.add_argument("--only", choices=["plain", "history"], default=None, help="run one pass only (default both)")
    p.add_argument("--baseline", type=Path, default=None, metavar="RESULTS_CSV",
                   help="results.csv of an existing sweep.py run; its all-runs rows are added to the table for comparison")
    p.add_argument("--cv", type=int, default=5, metavar="K", help="K-fold cross-validation grouped by run (default 5)")
    p.add_argument("--threshold", type=float, default=1062.0, help="pressure (N) that counts as a crossing (default 1062)")
    p.add_argument("--out-dir", type=Path, default=Path("fm_extra"))
    args = p.parse_args()
    windows = [int(x) for x in args.windows.split(",")]
    res = run_passes(load_data(args.data), windows, args.out_dir, args.cv, args.threshold, args.only)
    if args.baseline:
        base = pd.read_csv(args.baseline)
        base = base[(base.train_runs.astype(str) == "all") & base.window_s.isin(windows)]
        res = pd.concat([base, res], ignore_index=True)
    res.to_csv(args.out_dir / "results.csv", index=False)
    show(res)
    print(f"\nwrote {args.out_dir}/results.csv")


if __name__ == "__main__":
    main()
