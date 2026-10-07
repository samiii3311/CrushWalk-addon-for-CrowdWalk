#!/usr/bin/env python3
"""
sweep.py

Which window length should the model predict over, and is there enough data for it?
Runs force_model.py's pressure model at every window length x every training size and
draws one graph per model (Random Forest, XGBoost): one line per window, crush runs used
for training along the bottom, share of crush events warned in time up the side.

  a line that has gone flat    more data has stopped helping: that height is the window's score
  a dashed line                still rising at the largest size: that window needs more runs
  the ringed point             the pick: the longest flat window within FLAT_GAIN of the best

<out-dir>/results.csv (the table) and <out-dir>/sweep_<model>.png (the graphs) are rewritten
after every run, so a stopped sweep still leaves them. Each run's own files are in <out-dir>/h<window>_runs<N>/.

Usage (on the Linux machine):
    python3 sweep.py crush.duckdb --out-dir fm_sweep
    python3 sweep.py crush.duckdb --windows 60,120 --runs 16,32,0 --out-dir fm_sweep2
    python3 sweep.py --replot fm_sweep/results.csv      # redraw the graphs without rerunning
    python3 sweep.py --selftest
"""

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from force_model import TARGET, TICK, load_data, run

FLAT_GAIN = 0.05  # a window counts as "flat" when its last step in training size gained less than this share of events


def plot_sweep(res, out_dir, note=""):
    """One graph per model, <out_dir>/sweep_<model>.png."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed, so no graph (pip install matplotlib); results.csv is complete")
        return
    for model in res.model.unique():
        plot_model(plt, res[res.model == model], model, out_dir / f"sweep_{model}.png", note)


def plot_model(plt, res, model, path, note):
    """One line per window: events warned against crush runs trained on. Dashed = still rising."""
    windows = sorted(res.window_s.unique())
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    ends = {}
    for i, w in enumerate(windows):
        g = res[res.window_s == w].sort_values("crush_runs")
        y = g.warned_share.to_numpy() * 100
        rising = len(y) > 1 and y[-1] - y[-2] > FLAT_GAIN * 100
        colour = plt.cm.Blues(0.45 + 0.55 * i / max(1, len(windows) - 1))  # one hue, longer window = darker
        ax.plot(g.crush_runs, y, "--" if rising else "-", color=colour, lw=2, marker="o", ms=5)
        ax.annotate(f"  {w} s" + (" (still rising)" if rising else ""), (g.crush_runs.iloc[-1], y[-1]),
                    va="center", fontsize=11)
        ends[w] = (y[-1], rising, g.crush_runs.iloc[-1])
    flat = {w: e for w, e in ends.items() if not e[1]}
    if flat:
        best = max(e[0] for e in flat.values())
        pick = max(w for w, e in flat.items() if e[0] >= best - FLAT_GAIN * 100)
        ax.plot(ends[pick][2], ends[pick][0], "o", ms=15, mfc="none", mec="#E69F00", mew=2.5)
        title = f"{model}: pick {pick} s"
    else:
        title = f"{model}: no window has gone flat yet, more runs are needed"
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_xlabel("Crush runs used for training", fontsize=11)
    ax.set_ylabel("Crush events warned in time (%)", fontsize=11)
    ax.set_ylim(0, 100)
    ax.set_xlim(right=ax.get_xlim()[1] * 1.3)  # room for the line labels
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e4e4e2", lw=0.8)
    fig.text(0.01, 0.01, f"Ring = the longest flat window within {FLAT_GAIN * 100:.0f} points of the best. "
                         f"Dashed = still rising with more data. {note}", fontsize=8, color="#666666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    print(f"graph: {path}")


def sweep(df, windows, sizes, out_dir, cv=5, threshold=1062.0):
    """Every window length x every training size (0 = all runs)."""
    rows = []
    for h in windows:
        for n in sizes:
            s, _ = run(df, h, 0.2, out_dir / f"h{h}_runs{n or 'all'}", cv=cv, window_max=True,
                       train_runs=n or None, threshold=threshold, importance=False)
            for model, r in s.attrs["crossings"].items():
                rows.append({"window_s": h, "train_runs": n or "all", "crush_runs": s.attrs["crush_runs"],
                             "model": model, **r})
            res = pd.DataFrame(rows)
            res.to_csv(out_dir / "results.csv", index=False)
            plot_sweep(res, out_dir, f"{cv}-fold by run, crossing = {threshold:g} N.")
    return res


def selftest():
    # same toy data as force_model.py's selftest: force on a link follows upstream inflow 5 ticks earlier
    rng = np.random.default_rng(0)

    def one_run(sid, T=300):
        inflow = np.clip(np.sin(np.arange(T) / 25 + rng.uniform(0, 6)) * 3 + 3 + rng.normal(0, 0.3, T), 0, None)
        force = np.r_[np.zeros(5), inflow[:-5] * 200] + rng.normal(0, 20, T)
        return pd.DataFrame({"scenario_id": sid, TICK: np.arange(T), "link_id": "b", "prev_inflow": inflow, TARGET: force})

    df = pd.concat([one_run(f"r{i}") for i in range(6)], ignore_index=True)
    out = Path(tempfile.mkdtemp())
    res = sweep(df, [5, 10], [2, 0], out, cv=3, threshold=500)
    assert len(res) == 8 and res.warned_share.between(0, 1).all()  # 2 windows x 2 sizes x 2 models
    assert len(pd.read_csv(out / "results.csv")) == 8
    all_runs = res[res.train_runs == "all"]
    assert (all_runs.crush_runs >= res[res.train_runs == 2].crush_runs.max()).all()  # more runs, more crush runs
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Sweep window length x training size; write results.csv and sweep.png.")
    p.add_argument("data", type=Path, nargs="?", help="DuckDB file filled by link_context.py, or a CSV")
    p.add_argument("--windows", default="30,60,120,300,600", help="window lengths in ticks (default 30,60,120,300,600)")
    p.add_argument("--runs", default="8,16,24,32,0", help="training sizes in runs per fold; 0 = all (default 8,16,24,32,0)")
    p.add_argument("--cv", type=int, default=5, metavar="K", help="K-fold cross-validation grouped by run (default 5)")
    p.add_argument("--threshold", type=float, default=1062.0, help="pressure (N) that counts as a crossing (default 1062)")
    p.add_argument("--out-dir", type=Path, default=Path("fm_sweep"))
    p.add_argument("--replot", type=Path, default=None, metavar="RESULTS_CSV",
                   help="only redraw the graphs next to an existing results.csv")
    args = p.parse_args()
    if args.replot:
        return plot_sweep(pd.read_csv(args.replot), args.replot.parent)
    if not args.data:
        p.error("give the database, or --replot results.csv")
    sweep(load_data(args.data), [int(x) for x in args.windows.split(",")], [int(x) for x in args.runs.split(",")],
          args.out_dir, args.cv, args.threshold)


if __name__ == "__main__":
    main()
