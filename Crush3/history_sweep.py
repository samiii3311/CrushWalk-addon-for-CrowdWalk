#!/usr/bin/env python3
"""
history_sweep.py

How far back does the history need to go? The data and the window stay the same; only the
inputs change. Trains the two main models (models_main.py) once per look-back:

  back 0      no history columns at all
  back 10     the change over the last 10 ticks                       (<column>_d10)
  back 30     the changes over the last 10 and 30 ticks
  back 60     ... 10, 30 and 60 ticks: what models_main.py uses today
  back 120    ... and 120 ticks
  back 300    ... and 300 ticks

Each look-back adds to the one before, so this is 6 trainings and not every combination.

The rule for the pick, per model: the shortest look-back wins unless a longer one is clearly
better. Going through the look-backs from short to long, a longer one replaces the pick only if

  its typical warning time OR its share of real alarms is higher, with the whole 95% range of
  the difference above zero, and neither of the two is lower with its whole range below zero.

The difference is taken between the two models on the same test runs: the runs are re-drawn at
random 2000 times (error_bars.py) and both models are scored on the same re-draw each time.
That is a much narrower range than comparing the two models' own ranges.

What the pick does not cover: it was chosen on these test runs, so confirm it once on runs that
played no part in choosing it (a later seed) before it becomes a headline number.

<out-dir>/results.csv is rewritten after every look-back, so a stopped sweep still leaves it.
Each look-back's own files are in <out-dir>/back<N>/.

Runs in the background by default and shows its log (<out-dir>/run.log) live: Ctrl-C only stops
showing the log, `--stop` ends the job, `--foreground` runs it in the terminal (see background.py).

Usage (on the Linux machine):
    python3 history_sweep.py crush.duckdb --out-dir fm_history
    python3 history_sweep.py crush.duckdb --window 300 --back 60,120 --out-dir fm_history_h300
    python3 history_sweep.py --selftest
"""

import argparse
import contextlib
import io
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import background
import baselines
import models_main
import results
from error_bars import median_lead, pieces, real_share, redraw_diff
from force_model import TARGET, TICK, first_crossings, load_data, run

BACK = (0, 10, 30, 60, 120, 300)  # look-backs to try, in ticks (1 tick = 1 s)


def no_history(columns=None):
    """The main models, told not to expect history columns (look-back 0)."""
    return {name: (model, False) for name, (model, _) in models_main.new_models(columns).items()}


def clearly_better(d):
    """The rule at the top: d holds the 95% ranges of (longer look-back minus the pick)."""
    better = d["d_warning_s_lo"] > 0 or d["d_alarms_real_lo"] > 0
    worse = d["d_warning_s_hi"] < 0 or d["d_alarms_real_hi"] < 0
    return bool(better and not worse)


def sweep(df, backs, window, out_dir, cv=5, threshold=1062.0):
    backs = sorted(set(backs))
    # history and Holt columns once, for the longest look-back; each training drops what it may not see
    df = baselines.add_holt(models_main.add_lags(df, [b for b in backs if b]))
    ev = first_crossings(df, TARGET, threshold)
    parts, pick, rows = {}, {}, []
    for back in backs:
        too_far = [c for c in df.columns if models_main.is_history(c) and int(c.rpartition("_d")[2]) > back]
        s, oof = run(df.drop(columns=too_far), window, 0.2, out_dir / f"back{back}", cv=cv, window_max=True,
                     threshold=threshold, importance=False, models=models_main.new_models if back else no_history)
        parts[back], events, _ = pieces(oof, ev, window, threshold)
        for model in models_main.new_models():
            leads, alarms = parts[back][model]
            row = {"back_s": back, "model": model, "MAE": s.loc[model, ("MAE", "mean")], "events": events,
                   "events_warned": sum(len(x) for x in leads), "median_warning_s": median_lead(leads),
                   "alarms_real_share": real_share(alarms)}
            if model in pick:  # compare with the pick so far, on the same re-drawn runs
                old_leads, old_alarms = parts[pick[model]][model]
                (w_lo, w_hi), (a_lo, a_hi) = redraw_diff(old_leads, leads, median_lead), redraw_diff(old_alarms, alarms, real_share)
                row.update(vs_back_s=pick[model], d_warning_s=median_lead(leads) - median_lead(old_leads),
                           d_warning_s_lo=w_lo, d_warning_s_hi=w_hi,
                           d_alarms_real=real_share(alarms) - real_share(old_alarms), d_alarms_real_lo=a_lo, d_alarms_real_hi=a_hi)
                row["clearly_better"] = clearly_better(row)
            if model not in pick or row["clearly_better"]:
                pick[model] = back
            rows.append(row)
        res = pd.DataFrame(rows)
        res["pick"] = [pick[m] == b for m, b in zip(res.model, res.back_s)]
        out_dir.mkdir(parents=True, exist_ok=True)
        res.to_csv(out_dir / "results.csv", index=False)
    show(res)
    return res


def show(res):
    pct = lambda s: (s * 100).round(1)
    t = pd.DataFrame({"model": res.model, "back_s": res.back_s, "MAE": res.MAE.round(2),
                      "warned": res.events_warned.astype(str) + "/" + res.events.astype(str),
                      "warning_s": res.median_warning_s, "real_%": pct(res.alarms_real_share)})
    if "vs_back_s" in res:
        t["vs"] = res.vs_back_s
        t["d_warning_s"], t["lo"], t["hi"] = res.d_warning_s, res.d_warning_s_lo, res.d_warning_s_hi
        t["d_real_%"], t["lo "], t["hi "] = pct(res.d_alarms_real), pct(res.d_alarms_real_lo), pct(res.d_alarms_real_hi)
    t["pick"] = res.pick.map({True: "<-- pick", False: ""})
    print("\n== look-back by look-back; d_ = this row minus the row it is compared with (vs), lo/hi = 95% range of that difference ==")
    print(t.sort_values(["model", "back_s"], kind="stable").to_string(index=False, na_rep=""))
    for model, g in res[res.pick].groupby("model", sort=False):
        print(f"{model}: look back {g.back_s.iloc[0]} s")


def selftest():
    assert clearly_better({"d_warning_s_lo": 1, "d_warning_s_hi": 9, "d_alarms_real_lo": -0.1, "d_alarms_real_hi": 0.1})
    assert not clearly_better({"d_warning_s_lo": -1, "d_warning_s_hi": 9, "d_alarms_real_lo": -0.1, "d_alarms_real_hi": 0.1})
    assert not clearly_better({"d_warning_s_lo": 1, "d_warning_s_hi": 9, "d_alarms_real_lo": -0.3, "d_alarms_real_hi": -0.1})
    assert not clearly_better({"d_warning_s_lo": np.nan, "d_warning_s_hi": np.nan, "d_alarms_real_lo": np.nan, "d_alarms_real_hi": np.nan})
    # same toy data as sweep.py's selftest: force on a link follows upstream inflow 5 ticks earlier
    rng = np.random.default_rng(0)

    def one_run(sid, T=300):
        inflow = np.clip(np.sin(np.arange(T) / 25 + rng.uniform(0, 6)) * 3 + 3 + rng.normal(0, 0.3, T), 0, None)
        force = np.r_[np.zeros(5), inflow[:-5] * 200] + rng.normal(0, 20, T)
        return pd.DataFrame({"scenario_id": sid, TICK: np.arange(T), "link_id": "b", "prev_inflow": inflow, TARGET: force})

    df = pd.concat([one_run(f"r{i}") for i in range(6)], ignore_index=True)
    out = Path(tempfile.mkdtemp())
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        res = sweep(df, [5, 0, 1], 5, out, cv=3, threshold=500)
    log = log.getvalue()
    print(log)
    assert list(res.back_s) == [0, 0, 1, 1, 5, 5] and set(res.model) == set(models_main.new_models())
    assert res.pick.groupby(res.model).sum().eq(1).all()  # one pick per model
    assert res.vs_back_s[:2].isna().all() and res.vs_back_s[2:].notna().all()  # the shortest has nothing to be compared with
    # 2 columns have history (prev_inflow and the force) x the look-backs each training may see: none, then 1, then 1 and 5
    assert -1 < log.find("(+0 history)") < log.find("(+2 history)") < log.find("(+4 history)")
    assert len(pd.read_csv(out / "results.csv")) == 6
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Train the main models once per history look-back and pick the shortest that is enough.")
    p.add_argument("data", type=Path, help="DuckDB file filled by link_context.py, or a CSV")
    p.add_argument("--back", default=",".join(map(str, BACK)), help=f"look-backs in ticks, 0 = no history (default {','.join(map(str, BACK))})")
    p.add_argument("--window", type=int, default=120, help="window length in ticks the model predicts over (default 120)")
    p.add_argument("--cv", type=int, default=5, metavar="K", help="K-fold cross-validation grouped by run (default 5)")
    p.add_argument("--threshold", type=float, default=1062.0, help="pressure (N) that counts as a crossing (default 1062)")
    p.add_argument("--out-dir", type=Path, default=Path("fm_history"))
    background.stop_requested()
    background.add_args(p)
    args = p.parse_args()
    args.out_dir = results.new_dir(args.out_dir)
    background.start(args, args.out_dir / "run.log")
    sweep(load_data(args.data), [int(x) for x in args.back.split(",")], args.window, args.out_dir, args.cv, args.threshold)
    results.zip_up(args.out_dir)


if __name__ == "__main__":
    main()
