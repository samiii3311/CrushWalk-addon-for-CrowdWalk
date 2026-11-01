#!/usr/bin/env python3
"""
threshold_sweep.py

What does it cost to miss fewer events? (Prof. Sooksan, 2026-10: lower the alarm line until no
event is missed; at the extreme the model alarms on everything.)

Two lines that are the same number today are pulled apart here:

  event line (--threshold, 1062 N)   what counts as an event and as a real alarm. Never changes.
  alarm line (--lines)               the predicted pressure at which the model raises an alarm.

No model is trained: this reads a predictions.csv that force_model.py / sweep.py already saved and
rescores it at each alarm line. Per model and alarm line it prints

  missed            events with no alarm before them (of `events`)
  warning (s)       median warning, with its 95% range from re-drawing whole runs
  real %            share of alarms that were real, with its 95% range
  alarm counts      the numbers behind real % (Prof. Kulla, 2026-10), counted two ways:
                      ticks     one per link per tick in alarm (what real % is computed from)
                      stretches one per unbroken run of alarm ticks on one link; a stretch is
                                real if any of its ticks is real
                      groups    only with --gaps: stretches with gaps of up to that many seconds merged
                                (a flickering alarm is one alarm to staff); real the same way

At alarm line = event line every number must equal the crossings.csv the model wrote; the script
stops if it does not (wrong database or threshold).
`zero_missed` marks, per model, the highest alarm line with no missed event. It prints; you decide.

Usage (on the Linux machine):
    python3 Crush3/threshold_sweep.py /mnt/ssd_1tb/crush/crush_s1234_1way.duckdb fm_models_s1234/h120_runsall
    python3 Crush3/threshold_sweep.py <db> <folder> --lines 1062,1000,950,900 --out fm_threshold/results.csv
    python3 Crush3/threshold_sweep.py <db> <folder> --gaps 5,10,30    # also alarm groups with short gaps merged
    python3 Crush3/threshold_sweep.py --selftest
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import results
from error_bars import NOW, cell, cell_id, check
from force_model import TARGET, TICK, first_crossings, load_data


def groups(oof, call, real, gap):
    """Alarm groups on one link: alarm ticks with at most `gap` non-alarm ticks between them are one group.
    A group is real if any of its ticks is real. Returns (groups, real groups)."""
    c = oof.loc[call, ["scenario_id", "link_id", TICK]]
    same_link = c.scenario_id.eq(c.scenario_id.shift()) & c.link_id.eq(c.link_id.shift())
    new = ~same_link | (c[TICK].diff() > gap + 1)
    return int(new.sum()), int(real[call].groupby(new.cumsum()).any().sum())


def counts(oof, col, line, threshold, gaps=()):
    """Alarm ticks / real ticks / alarm stretches / real stretches for one model at one alarm line, plus
    alarm groups with short gaps merged (one pair of columns per gap). oof must be sorted by run, link, tick."""
    call = (oof[NOW] < threshold) & (oof[col] >= line)
    real = call & (oof.true >= threshold)
    out = {"alarm_ticks": int(call.sum()), "real_ticks": int(real.sum())}
    out["alarm_stretches"], out["real_stretches"] = groups(oof, call, real, 0)  # stretch = group with no gap
    for g in gaps:
        out[f"alarm_groups_g{g}"], out[f"real_groups_g{g}"] = groups(oof, call, real, g)
    return out


def sweep(oof, ev, horizon, threshold, lines, folder=None, gaps=()):
    oof = oof.sort_values(["scenario_id", "link_id", TICK], ignore_index=True)
    models = [c for c in oof.columns if c.startswith("pred_") and not c.startswith("pred_baseline")]
    rows = []
    for line in lines:
        # cell() alarms at pred >= threshold; raising every prediction by (threshold - line) makes that
        # pred >= line, while events, real alarms and the calm test stay on the event line.
        shifted = oof.assign(**{c: oof[c] + (threshold - line) for c in models})
        for row in cell(shifted, ev, horizon, threshold):
            if folder is not None and line == threshold:
                check(row, folder)
            rows.append({"alarm_line": line, **row, "missed": row["events"] - row["events_warned"],
                         **counts(oof, "pred_" + row["model"], line, threshold, gaps)})
    res = pd.DataFrame(rows)
    ok = res[res.missed == 0].groupby("model").alarm_line.max()
    res["zero_missed"] = res.alarm_line == res.model.map(ok)
    return res


def selftest():
    # link b crosses 500 at tick 3; link c never does. Model M says 450 on b before the crossing
    # and 450 on c for two ticks: alarm line 500 misses the event, 400 warns 2 ticks ahead and adds a false stretch.
    p = [0.0, 100, 200, 600, 700, 0]
    raw = pd.concat([pd.DataFrame({"scenario_id": "e", "link_id": "b", TICK: range(6), TARGET: p}),
                     pd.DataFrame({"scenario_id": "e", "link_id": "c", TICK: range(6), TARGET: 0.0})], ignore_index=True)
    oof = raw[["scenario_id", "link_id", TICK]].assign(
        true=[200.0, 600, 700, 700, 0, 0] + [0.0] * 6, **{NOW: raw[TARGET]},
        pred_M=[0.0, 450, 450, 600, 600, 0] + [0.0, 450, 450, 0, 450, 0])
    res = sweep(oof, first_crossings(raw, TARGET, 500), horizon=3, threshold=500, lines=[500, 400], gaps=[1]).set_index("alarm_line")
    hi, lo = res.loc[500], res.loc[400]
    assert hi.missed == 1 and hi.alarm_ticks == 0 and not hi.zero_missed
    assert lo.missed == 0 and lo.median_warning_s == 2 and lo.zero_missed
    assert (lo.alarm_ticks, lo.real_ticks, lo.alarm_stretches, lo.real_stretches) == (5, 2, 3, 1)
    # link c alarms at ticks 1, 2 and 4: two stretches, but one group once a 1-tick gap is merged
    assert (lo.alarm_groups_g1, lo.real_groups_g1) == (2, 1)
    assert lo.alarms == lo.alarm_ticks and np.isclose(lo.alarms_real_share, 2 / 5)  # same tick count as error_bars
    print("selftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Rescore saved predictions at lower alarm lines: missed events vs real alarms.")
    p.add_argument("data", type=Path, help="the DuckDB file (or CSV) the model was run on")
    p.add_argument("folder", type=Path, help="folder with predictions.csv, e.g. fm_models_s1234/h120_runsall")
    p.add_argument("--threshold", type=float, default=1062.0, help="event line in N (default 1062)")
    p.add_argument("--lines", help="alarm lines in N, comma-separated (default: event line down to 500 in 25 N steps)")
    p.add_argument("--window", type=int, help="window in s (default: read from a folder named h<window>_runs<N>)")
    p.add_argument("--gaps", default="",
                   help="optional: also count alarm groups with gaps up to this many s merged, comma-separated, "
                        "e.g. 5,10,30 (default: off, the table as before)")
    p.add_argument("--out", type=Path, default=Path("fm_threshold/results.csv"))
    args = p.parse_args()
    folder = results.find(args.folder)
    horizon = args.window or (cell_id(folder.name) or (None,))[0]
    if horizon is None:
        sys.exit(f"{folder}: name is not h<window>_runs<N>; give --window")
    lines = ([float(x) for x in args.lines.split(",")] if args.lines
             else list(np.arange(args.threshold, 499, -25.0)))
    if args.threshold not in lines:
        lines.insert(0, args.threshold)  # always include the current setting, which is also the check
    raw = load_data(args.data)
    ev = first_crossings(raw.assign(link_id=raw.link_id.astype(str)), TARGET, args.threshold)
    oof = pd.read_csv(folder / "predictions.csv", dtype={"scenario_id": str, "link_id": str})
    gaps = [int(g) for g in args.gaps.split(",")] if args.gaps else []
    res = sweep(oof, ev, horizon, args.threshold, sorted(lines, reverse=True), folder, gaps)
    out = results.new_dir(args.out.parent) / args.out.name
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False)
    show = res.assign(real_pct=res.alarms_real_share * 100, r_lo=res.alarms_real_share_lo * 100,
                      r_hi=res.alarms_real_share_hi * 100, w_lo=res.median_warning_s_lo,
                      warning=res.median_warning_s, w_hi=res.median_warning_s_hi)
    cols = ["alarm_line", "missed", "events", "w_lo", "warning", "w_hi", "r_lo", "real_pct", "r_hi",
            "real_ticks", "alarm_ticks", "real_stretches", "alarm_stretches",
            *[f"{k}_groups_g{g}" for g in gaps for k in ("real", "alarm")], "zero_missed"]
    for model, t in show.groupby("model", sort=False):
        print(f"\n== {model}, {horizon} s window, event line {args.threshold:g} N ==")
        print(t[cols].round(1).to_string(index=False))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
