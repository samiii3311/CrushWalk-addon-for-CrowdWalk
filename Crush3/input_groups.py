#!/usr/bin/env python3
"""
input_groups.py

Which inputs can go, and which could be merged into one? (E6, Prof. Sooksan's aims 1 and 3.)

A ranking of single inputs misleads here: density, agent_count, queue_count, the neighbours'
densities and the pressure columns carry much the same information, so shuffling one of them
barely hurts (the others stand in) and all of them look unimportant. This script tests groups instead:

  1. Groups: inputs whose rank correlation (Spearman |rho|) is high are put in one group. Complete
     linkage, cut at --cut on 1 - |rho|: every pair inside a group has |rho| >= 1 - cut.
  2. For each group, retrain without it ("drop"), and for groups of two or more also with only one
     member left ("keep one": the member most correlated with the rest). "Keep one" losing nothing
     means the group can be one input; "drop" losing nothing means the group is not needed at all.
  3. Every training is scored like history_sweep.py: events missed, warning time, share of real
     alarms, and the difference to "all inputs" with its 95% range on the same re-drawn test runs.

The script prints the table and does not choose. Reading it (same rule as the look-back):
  missed events first; then a change is fine if both warning time and real alarms stay within one
  standard error of the best row (1se = wr). Confirm the final input set once on seeds 3-4.
Aim 2 (inputs that are missing) does not come out of this table; it comes from the events the
model misses or warns late.

First look at the groups only (a few minutes for loading, no training, runs in the terminal), at several cuts, and choose --cut from that:
    python3 input_groups.py crush.duckdb --back 30 --groups-only
Then train (both models, or one model per part with --models; RF and XGB may use different --back):
    python3 input_groups.py crush.duckdb --back 30 --cut 0.2 --models RF_history --out-dir fm_groups_rf
    python3 input_groups.py --selftest

<out-dir>/results.csv is rewritten after every training; groups.csv lists the groups.
Runs in the background by default and shows its log live (see background.py).
"""

import argparse
import contextlib
import io
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

import background
import baselines
import models_main
import results
from error_bars import median_lead, pieces, real_share, redraw_diff
from force_model import NOT_FEATURES, TARGET, first_crossings, load_data, run
from history_sweep import measures, one_se

CUTS = (0.1, 0.2, 0.3, 0.4, 0.5)  # shown by --groups-only so the cut is chosen by looking


def inputs(df):
    """The same input list force_model.run() builds."""
    return [c for c in df.columns if c not in NOT_FEATURES and not c.startswith(baselines.HOLT_PREFIX)
            and pd.api.types.is_numeric_dtype(df[c])]


def correlation(df, cols, rows=200_000):
    # ponytail: rank correlation on a sample of all runs, test runs included; it never sees the target,
    # so the leak is small. Compute it per fold if a group result becomes a headline number.
    rho = df[cols].sample(min(rows, len(df)), random_state=0).corr("spearman").abs()
    return rho.fillna(0.0).where(~np.eye(len(cols), dtype=bool), 1.0)  # constant column: unrelated to the rest


def groups(rho, cut):
    """Lists of inputs; inside each, every pair has |rho| >= 1 - cut. Biggest group first."""
    z = linkage(squareform(1 - rho.to_numpy(), checks=False), "complete")
    label = fcluster(z, cut, "distance")
    found = [list(rho.index[label == k]) for k in np.unique(label)]
    return sorted(found, key=lambda g: (-len(g), g[0]))


def keep_one(rho, group):
    """The member most correlated with the rest of its group."""
    return rho.loc[group, group].sum().idxmax()


def show_groups(rho, cut):
    for g in groups(rho, cut):
        if len(g) > 1:
            print(f"  {len(g):2d}: {', '.join(g)}  (keep one: {keep_one(rho, g)}, lowest |rho| inside {rho.loc[g, g].min().min():.2f})")
    print(f"  plus {sum(len(g) == 1 for g in groups(rho, cut))} inputs alone")


def sweep(df, backs, window, out_dir, cut, cv=5, threshold=1062.0, names=None):
    df = baselines.add_holt(models_main.add_lags(df, backs))
    cols = inputs(df)
    rho = correlation(df, cols)
    found = groups(rho, cut)
    print(f"\n== {len(found)} groups at cut {cut} (every pair inside has |rho| >= {1 - cut:.2f}) ==")
    show_groups(rho, cut)
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"group": i, "input": c, "keep_one": c == keep_one(rho, g)} for i, g in enumerate(found) for c in g]
                 ).to_csv(out_dir / "groups.csv", index=False)

    tests = [("all inputs", "", [])]  # (name, group number, inputs removed)
    for i, g in enumerate(found):
        tests.append((f"drop g{i}", i, g))
        if len(g) > 1:
            tests.append((f"keep one g{i}", i, [c for c in g if c != keep_one(rho, g)]))
    ev = first_crossings(df, TARGET, threshold)
    models = (lambda columns=None: {n: models_main.new_models(columns)[n] for n in names}) if names else models_main.new_models
    parts, rows = {}, []
    for name, group, removed in tests:
        keep = [c for c in cols if c not in removed]  # the keep-list keeps run() from re-adding dropped history columns
        s, oof = run(df, window, 0.2, out_dir / name.replace(" ", "_"), cv=cv, window_max=True, threshold=threshold,
                     top_features=keep, importance=False, models=models)
        parts[name], events, _ = pieces(oof, ev, window, threshold)
        for model in names or list(models_main.new_models()):
            row = {"test": name, "group": group, "removed": ", ".join(removed), "model": model,
                   **measures(s, parts[name], model, events)}
            if name != "all inputs":  # difference to all inputs, on the same re-drawn runs
                (old_l, old_a), (new_l, new_a) = parts["all inputs"][model], parts[name][model]
                (w_lo, w_hi), (a_lo, a_hi) = redraw_diff(old_l, new_l, median_lead), redraw_diff(old_a, new_a, real_share)
                row.update(d_warning_s=median_lead(new_l) - median_lead(old_l), d_warning_s_lo=w_lo, d_warning_s_hi=w_hi,
                           d_alarms_real=real_share(new_a) - real_share(old_a), d_alarms_real_lo=a_lo, d_alarms_real_hi=a_hi)
            rows.append(row)
        res = one_se(pd.DataFrame(rows))
        res.to_csv(out_dir / "results.csv", index=False)
    show(res)
    return res


def show(res):
    pct = lambda s: (s * 100).round(1)
    t = pd.DataFrame({"model": res.model, "test": res.test, "MAE": res.MAE.round(2),
                      "missed": res.events_missed.astype(str) + " of " + res.events.astype(str),
                      "warning_s": res.median_warning_s, "se_w": res.warning_se.round(1),
                      "real_%": pct(res.alarms_real_share), "se_r": pct(res.real_se),
                      "1se": res.within_warning.map({True: "w", False: "-"}) + res.within_real.map({True: "r", False: "-"})})
    if "d_warning_s" in res:
        t["d_warning_s"], t["lo"], t["hi"] = res.d_warning_s, res.d_warning_s_lo, res.d_warning_s_hi
        t["d_real_%"], t["lo "], t["hi "] = pct(res.d_alarms_real), pct(res.d_alarms_real_lo), pct(res.d_alarms_real_hi)
    print("\n== each group dropped, or cut to one member; d_ = this row minus all inputs, lo/hi = its 95% range ==")
    print(t.to_string(index=False, na_rep=""))
    print("1se: w / r = within one standard error of the best row on warning / real alarms, among the rows that miss")
    print("the fewest events. Group members are in groups.csv.")


def selftest():
    # toy data as in history_sweep.py, plus a near-copy of the inflow and a column of pure noise
    rng = np.random.default_rng(0)

    def one_run(sid, T=300):
        inflow = np.clip(np.sin(np.arange(T) / 25 + rng.uniform(0, 6)) * 3 + 3 + rng.normal(0, 0.3, T), 0, None)
        force = np.r_[np.zeros(5), inflow[:-5] * 200] + rng.normal(0, 20, T)
        return pd.DataFrame({"scenario_id": sid, "current_traveling_period": np.arange(T), "link_id": "b",
                             "prev_inflow": inflow, "inflow_copy": inflow + rng.normal(0, 0.05, T),
                             "noise": rng.normal(0, 1, T), TARGET: force})

    df = pd.concat([one_run(f"r{i}") for i in range(6)], ignore_index=True)
    rho = correlation(df, ["prev_inflow", "inflow_copy", "noise"])
    g = groups(rho, 0.2)
    assert ["inflow_copy", "prev_inflow"] in g and ["noise"] in g, g
    out = Path(tempfile.mkdtemp())
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        res = sweep(df, [5], 5, out, 0.2, cv=3, threshold=500, names=["RF_history"])
    print(log.getvalue()[log.getvalue().find("== each"):])
    found = pd.read_csv(out / "groups.csv")
    n_groups, n_multi = found.group.nunique(), (found.group.value_counts() > 1).sum()
    assert len(res) == 1 + n_groups + n_multi and res.test.iloc[0] == "all inputs"  # one row per test, RF only
    assert res.d_warning_s.iloc[1:].notna().all() and np.isnan(res.d_warning_s.iloc[0])
    assert found.groupby("group").keep_one.sum().eq(1).all()  # exactly one kept member per group
    # 6 inputs: inflow, copy, noise, force, and the 5-tick change of inflow and force; "drop" of the
    # one-member noise group trains on the other 5 (the dropped columns are not re-added)
    assert "kept 6:" in log.getvalue() and "kept 5:" in log.getvalue()
    assert len(pd.read_csv(out / "results.csv")) == len(res)
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Retrain with each group of correlated inputs dropped or cut to one member.")
    p.add_argument("data", type=Path, help="DuckDB file filled by link_context.py, or a CSV")
    p.add_argument("--back", default=",".join(map(str, models_main.LAGS)),
                   help="history look-backs in ticks, the ones chosen by the look-back search (default %(default)s)")
    p.add_argument("--cut", type=float, help="group cut on 1 - |rho|; choose it after --groups-only")
    p.add_argument("--groups-only", action="store_true", help="print the groups at several cuts and stop (no training)")
    p.add_argument("--window", type=int, default=120, help="window length in ticks (default 120)")
    p.add_argument("--cv", type=int, default=5, metavar="K", help="K-fold cross-validation grouped by run (default 5)")
    p.add_argument("--threshold", type=float, default=1062.0, help="pressure (N) that counts as a crossing (default 1062)")
    p.add_argument("--models", help="only these models, comma-separated (default RF_history,XGB_history)")
    p.add_argument("--out-dir", type=Path, default=Path("fm_groups"))
    background.stop_requested()
    background.add_args(p)
    args = p.parse_args()
    backs = [int(x) for x in args.back.split(",") if int(x)]
    if not backs:  # run() would quietly add the default look-backs back
        p.error("--back needs at least one look-back above 0; these are the models with history")
    if args.groups_only:
        df = models_main.add_lags(load_data(args.data), backs)
        rho = correlation(df, inputs(df))
        for cut in CUTS:
            print(f"\n== cut {cut}: every pair inside a group has |rho| >= {1 - cut:.2f} ==")
            show_groups(rho, cut)
        return
    if args.cut is None:
        p.error("--cut is needed for training; look at --groups-only first")
    args.out_dir = results.new_dir(args.out_dir)
    background.start(args, args.out_dir / "run.log")
    sweep(load_data(args.data), backs, args.window, args.out_dir, args.cut, args.cv, args.threshold,
          args.models.split(",") if args.models else None)
    results.zip_up(args.out_dir)


if __name__ == "__main__":
    main()
