#!/usr/bin/env python3
"""
error_bars.py

How much could the sweep's numbers move by chance? No model is trained: this reads the
predictions.csv that sweep.py already saved for each window (the folders
trained on all runs, h<window>_runsall/) and gives a 95% range for

  median_warning_s     typical warning before an event
  alarms_real_share    share of alarms that were real

by re-drawing the runs at random, with replacement, REPS times and recomputing the number each
time. Whole runs are re-drawn, not single events or records: the events of one run are not
independent of each other. The range is the middle 95% of those re-draws.

Each number is first recomputed from the predictions and compared with the crossings.csv the
model wrote, so a mismatch (wrong database, changed threshold) is reported instead of plotted.

Writes one table that sweep_plot.py reads; with the _lo / _hi columns in it, its bar graphs
get a black line through each bar.

Usage (on the Linux machine):
    python3 error_bars.py crush.duckdb fm_sweep_1way fm_extra/history --out fm_errorbars/results.csv
    python3 sweep_plot.py fm_errorbars/results.csv --models RandomForest,XGBoost,RF_history,XGB_history
    python3 error_bars.py --selftest
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from force_model import TARGET, TICK, first_crossings, load_data

REPS = 2000
NOW = "pred_baseline: now"


def redraw(groups, stat, reps=REPS, seed=0):
    """95% range of stat(groups) when the groups (one per run) are re-drawn with replacement."""
    rng = np.random.default_rng(seed)
    n = len(groups)
    vals = [stat([groups[i] for i in rng.integers(0, n, n)]) for _ in range(reps)]
    return np.nanpercentile(vals, [2.5, 97.5])


def median_lead(groups):
    leads = np.concatenate(groups) if groups else np.array([])
    return np.median(leads) if len(leads) else np.nan


def real_share(groups):
    alarms, real = np.sum(groups, axis=0)
    return real / alarms if alarms else np.nan


def cell(oof, ev, horizon, threshold):
    """One row per model for one window: the two numbers and their ranges. Same definitions as force_model.run()."""
    keys = list(ev.index.names)
    o = oof.join(ev, on=keys, how="inner")
    o = o.assign(lead=o.cross_tick - o[TICK])
    o = o[(o.lead > 0) & (o.lead <= horizon)]
    event_runs = sorted(o.scenario_id.unique())
    calm = oof[NOW] < threshold
    rows = []
    for col in [c for c in oof.columns if c.startswith("pred_") and not c.startswith("pred_baseline")]:
        lead = o[o[col] >= threshold].groupby(keys).lead.max()  # one warning time per warned event
        by_run = lead.groupby("scenario_id").apply(lambda s: s.to_numpy())
        leads = [by_run.get(r, np.array([])) for r in event_runs]
        call = calm & (oof[col] >= threshold)
        counts = pd.DataFrame({"alarms": call, "real": call & (oof.true >= threshold)}).groupby(oof.scenario_id).sum()
        alarms = list(counts.to_numpy())
        (w_lo, w_hi), (a_lo, a_hi) = redraw(leads, median_lead), redraw(alarms, real_share)
        rows.append({"model": col[len("pred_"):], "events": o.groupby(keys).ngroups, "events_warned": len(lead),
                     "event_runs": len(event_runs), "alarms": int(counts.alarms.sum()),
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


def selftest():
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
    print("selftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="95% ranges for warning time and share of real alarms, from saved predictions.")
    p.add_argument("data", type=Path, help="the DuckDB file (or CSV) the sweep was run on")
    p.add_argument("folders", type=Path, nargs="+", help="sweep.py out-dirs, e.g. fm_sweep_1way fm_extra/history")
    p.add_argument("--threshold", type=float, default=1062.0, help="pressure (N) that counts as a crossing (default 1062)")
    p.add_argument("--out", type=Path, default=Path("fm_errorbars/results.csv"))
    args = p.parse_args()
    raw = load_data(args.data)
    ev = first_crossings(raw.assign(link_id=raw.link_id.astype(str)), TARGET, args.threshold)
    rows = []
    for top in args.folders:
        cells = sorted(top.glob("h*_runsall/predictions.csv"), key=lambda f: int(re.match(r"h(\d+)_", f.parent.name)[1]))
        if not cells:
            sys.exit(f"{top}: no h<window>_runsall/predictions.csv inside")
        for f in cells:
            horizon = int(re.match(r"h(\d+)_", f.parent.name)[1])
            print(f"{f.parent} ...", flush=True)
            oof = pd.read_csv(f, dtype={"scenario_id": str, "link_id": str})
            for row in cell(oof, ev, horizon, args.threshold):
                check(row, f.parent)
                # crush_runs is only there so sweep_plot.py can pick "the largest size"; there is one size here
                rows.append({"window_s": horizon, "train_runs": "all", "crush_runs": 0.0, **row})
    res = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.out, index=False)
    show = res.assign(warning=res.median_warning_s, w_lo=res.median_warning_s_lo, w_hi=res.median_warning_s_hi,
                      real_pct=res.alarms_real_share * 100, r_lo=res.alarms_real_share_lo * 100,
                      r_hi=res.alarms_real_share_hi * 100)
    print(f"\n== 95% ranges ({REPS} re-draws of the runs) ==")
    print(show[["model", "window_s", "events", "event_runs", "w_lo", "warning", "w_hi", "r_lo", "real_pct", "r_hi"]]
          .round(1).to_string(index=False))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
