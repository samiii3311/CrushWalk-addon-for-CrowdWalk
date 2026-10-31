#!/usr/bin/env python3
"""
error_bars.py

How much could the sweep's numbers move by chance? No model is trained: this reads the
predictions.csv that sweep.py already saved for each window and training size (the folders
h<window>_runs<N>/) and gives a 95% range for

  median_warning_s     typical warning before an event
  alarms_real_share    share of alarms that were real

by re-drawing the runs at random, with replacement, REPS times and recomputing the number each
time. Whole runs are re-drawn, not single events or records: the events of one run are not
independent of each other. The range is the middle 95% of those re-draws.

Each number is first recomputed from the predictions and compared with the crossings.csv the
model wrote, so a mismatch (wrong database, changed threshold) is reported instead of plotted.

Writes one table that sweep_plot.py reads; with the _lo / _hi columns in it, its bar graphs
get a black line through each bar and its data-size graphs a line through each point.

What the range covers: which runs happened to be tested. For a point trained on fewer runs it
does NOT cover which training runs were picked (sweep.py picks one fixed set per size), so a
bump in a data-size line can still be the luck of that pick.

--all-runs-only reads just the h<window>_runsall/ folders, as this script did at first (faster).

Usage (on the Linux machine):
    python3 error_bars.py crush.duckdb fm_sweep_1way fm_extra/history --out fm_errorbars/results.csv
    python3 error_bars.py crush.duckdb fm_models --all-runs-only --out fm_models_ranges/results.csv
    python3 sweep_plot.py fm_errorbars/results.csv --models RandomForest,XGBoost,RF_history,XGB_history
    python3 error_bars.py --selftest
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import results
from force_model import TARGET, TICK, first_crossings, load_data

REPS = 2000
NOW = "pred_baseline: now"


def redraw_values(groups, stat, reps=REPS, seed=0):
    """stat(groups) on each of `reps` re-draws of the groups (one per run), with replacement."""
    rng = np.random.default_rng(seed)
    n = len(groups)
    return [stat([groups[i] for i in rng.integers(0, n, n)]) for _ in range(reps)]


def redraw(groups, stat, reps=REPS, seed=0):
    """95% range of stat(groups) when the groups (one per run) are re-drawn with replacement."""
    return np.nanpercentile(redraw_values(groups, stat, reps, seed), [2.5, 97.5])


def redraw_se(groups, stat, reps=REPS, seed=0):
    """Standard error of stat(groups): its spread over the re-draws (used by the one-standard-error rule)."""
    return np.nanstd(redraw_values(groups, stat, reps, seed))


def redraw_diff(a, b, stat, reps=REPS, seed=0):
    """95% range of stat(b) - stat(a) when the runs are re-drawn and both are scored on the same re-draw.
    a and b hold one item per run, in the same order (two models tested on the same runs)."""
    return redraw(list(zip(a, b)), lambda g: stat([y for _, y in g]) - stat([x for x, _ in g]), reps, seed)


def median_lead(groups):
    leads = np.concatenate(groups) if groups else np.array([])
    return np.median(leads) if len(leads) else np.nan


def real_share(groups):
    alarms, real = np.sum(groups, axis=0)
    return real / alarms if alarms else np.nan


def read_predictions(path, like=None):
    """A saved predictions.csv; the run and link ids get the dtypes they have in `like` (the training data)."""
    oof = pd.read_csv(path, dtype={"scenario_id": str, "link_id": str})
    if like is not None:
        oof = oof.astype({k: like[k].dtype for k in ("scenario_id", "link_id") if k in like and k in oof})
    return oof


def pieces(oof, ev, horizon, threshold):
    """What the two numbers are made of, one item per run so that the runs can be re-drawn:
    model -> (warning times of its warned events, one array per run with an event; [alarms, real alarms], one pair per run).
    Also returns the number of events and of runs with an event. Same definitions as force_model.run()."""
    keys = list(ev.index.names)
    o = oof.join(ev, on=keys, how="inner")
    o = o.assign(lead=o.cross_tick - o[TICK])
    o = o[(o.lead > 0) & (o.lead <= horizon)]
    event_runs = sorted(o.scenario_id.unique())
    calm = oof[NOW] < threshold
    out = {}
    for col in [c for c in oof.columns if c.startswith("pred_") and not c.startswith("pred_baseline")]:
        lead = o[o[col] >= threshold].groupby(keys).lead.max()  # one warning time per warned event
        by_run = lead.groupby("scenario_id").apply(lambda s: s.to_numpy())
        call = calm & (oof[col] >= threshold)
        counts = pd.DataFrame({"alarms": call, "real": call & (oof.true >= threshold)}).groupby(oof.scenario_id).sum()
        out[col[len("pred_"):]] = ([by_run.get(r, np.array([])) for r in event_runs], list(counts.to_numpy()))
    return out, o.groupby(keys).ngroups, len(event_runs)


def cell(oof, ev, horizon, threshold):
    """One row per model for one window: the two numbers and their ranges."""
    parts, events, event_runs = pieces(oof, ev, horizon, threshold)
    rows = []
    for model, (leads, alarms) in parts.items():
        (w_lo, w_hi), (a_lo, a_hi) = redraw(leads, median_lead), redraw(alarms, real_share)
        rows.append({"model": model, "events": events, "events_warned": sum(len(x) for x in leads),
                     "event_runs": event_runs, "alarms": int(sum(a[0] for a in alarms)),
                     "median_warning_s": median_lead(leads), "median_warning_s_lo": w_lo, "median_warning_s_hi": w_hi,
                     "alarms_real_share": real_share(alarms), "alarms_real_share_lo": a_lo, "alarms_real_share_hi": a_hi})
    return rows


def check(row, folder):
    """Compare the recomputed numbers with what the model itself wrote for this folder."""
    path = folder / "crossings.csv"
    if not path.exists():
        return print(f"  [!] {folder}: no crossings.csv to check against")
    saved = pd.read_csv(path, index_col=0)
    if row["model"] not in saved.index:
        return print(f"  [!] {folder}: {row['model']} is not in crossings.csv")
    s = saved.loc[row["model"]]
    for col in ("median_warning_s", "alarms_real_share", "events"):
        if not np.isclose(row[col], s[col], rtol=1e-6, equal_nan=True):
            sys.exit(f"{folder}: {row['model']} {col} recomputed as {row[col]}, but crossings.csv says {s[col]}. "
                     "Is this the database and threshold the sweep used?")


def cell_id(folder_name):
    """'h120_runs48' -> (120, '48'); 'h30_runsall' -> (30, 'all'). None for any other folder."""
    m = re.fullmatch(r"h(\d+)_runs(\d+|all)", folder_name)
    return (int(m[1]), m[2]) if m else None


def crush_runs(top):
    """(window, training size) -> crush runs trained on, from the sweep's own results.csv: the x-axis of the
    data-size graphs. Empty if the folder has no results.csv."""
    if not (top / "results.csv").exists():
        return {}
    r = pd.read_csv(top / "results.csv")
    return {(int(w), str(n)): float(c) for w, n, c in zip(r.window_s, r.train_runs, r.crush_runs)}


def selftest():
    assert cell_id("h120_runs48") == (120, "48") and cell_id("h30_runsall") == (30, "all") and cell_id("pressure") is None
    # event timing as in force_model's selftest: link crosses 500 at tick 3, alarm from tick 1 = a 2-tick warning
    raw = pd.DataFrame({"scenario_id": "e", "link_id": "b", TICK: range(6), TARGET: [0.0, 100, 200, 600, 700, 0]})
    oof = raw[["scenario_id", "link_id", TICK]].assign(true=[200.0, 600, 700, 700, 0, 0], **{NOW: raw[TARGET]},
                                                       pred_M=[0.0, 600, 600, 600, 600, 0])
    (row,) = cell(oof, first_crossings(raw, TARGET, 500), horizon=3, threshold=500)
    assert row["median_warning_s"] == 2 and row["events"] == row["events_warned"] == 1
    assert row["alarms"] == 2 and row["alarms_real_share"] == 1.0  # ticks 1 and 2; ticks 3, 4 are not calm
    assert row["median_warning_s_lo"] == row["median_warning_s_hi"] == 2  # one run: every re-draw is the same
    # re-drawing: identical runs give no spread, different runs give a range that contains the measured value
    assert list(redraw([np.array([5.0])] * 4, median_lead, reps=50)) == [5.0, 5.0]
    lo, hi = redraw([np.array([v]) for v in (10.0, 20, 30, 40, 50)], median_lead, reps=500)
    assert 10 <= lo < 30 < hi <= 50
    lo, hi = redraw([np.array([10, 2]), np.array([10, 8])], real_share, reps=200)
    assert (lo, hi) == (0.2, 0.8)
    # two models on the same runs: always 3 s apart, so the difference has no spread however the runs fall
    a = [np.array([v]) for v in (10.0, 20, 30, 40, 50)]
    assert list(redraw_diff(a, [x + 3 for x in a], median_lead, reps=200)) == [3.0, 3.0]
    print("selftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="95% ranges for warning time and share of real alarms, from saved predictions.")
    p.add_argument("data", type=Path, help="the DuckDB file (or CSV) the sweep was run on")
    p.add_argument("folders", type=Path, nargs="+", help="sweep.py out-dirs, e.g. fm_sweep_1way fm_extra/history")
    p.add_argument("--threshold", type=float, default=1062.0, help="pressure (N) that counts as a crossing (default 1062)")
    p.add_argument("--out", type=Path, default=Path("fm_errorbars/results.csv"))
    p.add_argument("--all-runs-only", action="store_true", help="only the folders trained on all runs (faster)")
    args = p.parse_args()
    args.folders = [results.find(f) for f in args.folders]  # short names are looked up in the results folder
    args.out = results.new_dir(args.out.parent) / args.out.name  # fm_errorbars/results.csv -> <results folder>/<date>_fm_errorbars/
    raw = load_data(args.data)
    ev = first_crossings(raw.assign(link_id=raw.link_id.astype(str)), TARGET, args.threshold)
    rows = []
    for top in args.folders:
        trained_on = crush_runs(top)
        cells = [f for f in top.glob("h*_runs*/predictions.csv") if cell_id(f.parent.name)]
        if args.all_runs_only or not trained_on:  # without results.csv the smaller sizes have no place on the x-axis
            cells = [f for f in cells if cell_id(f.parent.name)[1] == "all"]
        if not cells:
            sys.exit(f"{top}: no h<window>_runs<N>/predictions.csv inside")
        # by window, then training size with "all" last
        for f in sorted(cells, key=lambda f: (cell_id(f.parent.name)[0], float(cell_id(f.parent.name)[1].replace("all", "inf")))):
            horizon, size = cell_id(f.parent.name)
            print(f"{f.parent} ...", flush=True)
            oof = read_predictions(f)
            for row in cell(oof, ev, horizon, args.threshold):
                check(row, f.parent)
                rows.append({"window_s": horizon, "train_runs": size, "crush_runs": trained_on.get((horizon, size), 0.0), **row})
    res = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.out, index=False)
    show = res.assign(warning=res.median_warning_s, w_lo=res.median_warning_s_lo, w_hi=res.median_warning_s_hi,
                      real_pct=res.alarms_real_share * 100, r_lo=res.alarms_real_share_lo * 100,
                      r_hi=res.alarms_real_share_hi * 100)
    print(f"\n== 95% ranges ({REPS} re-draws of the runs) ==")
    print(show[["model", "window_s", "train_runs", "events", "event_runs", "w_lo", "warning", "w_hi", "r_lo", "real_pct", "r_hi"]]
          .round(1).to_string(index=False))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
