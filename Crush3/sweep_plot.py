#!/usr/bin/env python3
"""
sweep_plot.py

Readable graphs from the results.csv that sweep.py writes. It only reads that table and
draws it: no model is run and nothing is recomputed. The numbers it plots are printed first,
so every point on a graph can be checked against the table.

Both graphs show the typical warning time: how many seconds before a crush event the model's
first alarm came (median_warning_s in results.csv).

  sweep_data_<model>.png   does more data help? One line per window, crush runs used for
                           training along the bottom.
  sweep_warning.png        how far ahead? One bar per window and model, trained on the
                           largest size.
  sweep_alarms.png         the same bars for the share of alarms that were real
                           (alarms_real_share in results.csv).

sweep.py's own graph plots warned_share, which came out near 100% for every window on the
114-run sweep, so its lines sit on top of each other. Warning time differs between windows.

Usage:
    python3 sweep_plot.py fm_sweep_1way/results.csv        # graphs go next to the csv
    python3 sweep_plot.py fm_extra/results.csv --models RandomForest,XGBoost,RF_history,XGB_history
    python3 sweep_plot.py --selftest
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

COLOURS = {"RandomForest": "#2171b5", "XGBoost": "#E69F00", "RF_history": "#08306b", "XGB_history": "#a66a00"}
NAMES = {"RF_history": "RandomForest + history", "XGB_history": "XGBoost + history"}  # legend text, if not the model's own name
# the bar graphs: file name -> (results.csv column, multiplier, title, y-axis label)
BARS = {"sweep_warning.png": ("median_warning_s", 1, "Warning time by window, trained on all runs",
                              "Typical warning before an event (s)"),
        "sweep_alarms.png": ("alarms_real_share", 100, "Alarms that were real, by window, trained on all runs",
                             "Alarms that were real (%)")}
# one colour and marker per window (Okabe-Ito colours, distinguishable with colour-blindness)
LINE_COLOURS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7"]
MARKERS = ["o", "s", "^", "D", "v"]


def label(window_s):
    return f"{window_s} s" if window_s < 120 else f"{window_s // 60} min"


def largest(res):
    """The row trained on the most data for each model and window."""
    return res.loc[res.groupby(["model", "window_s"]).crush_runs.idxmax()]


def plot_data(plt, res, model, path):
    r = res[res.model == model]
    windows = sorted(r.window_s.unique())
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    lines = []
    for i, w in enumerate(windows):
        g = r[r.window_s == w].sort_values("crush_runs")
        y = g.median_warning_s.to_numpy()
        # a colour and a marker shape per window, so the legend can be read without relying on colour alone
        line, = ax.plot(g.crush_runs, y, color=LINE_COLOURS[i % len(LINE_COLOURS)], lw=2,
                        marker=MARKERS[i % len(MARKERS)], ms=6, label=f"{label(w)}  ({y[-1]:.0f} s)")
        lines.append((y[-1], line))
    lines.sort(key=lambda t: -t[0])  # legend top to bottom = line ends top to bottom
    ax.legend([l for _, l in lines], [l.get_label() for _, l in lines], title="Window (value at the right end)",
              loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=11, title_fontsize=10)
    ax.set_title(f"{model}: does more data help?", loc="left", fontsize=13, fontweight="bold")
    ax.set_xlabel("Crush runs used for training", fontsize=11)
    ax.set_ylabel("Typical warning before an event (s)", fontsize=11)
    ax.set_ylim(0, max(100, r.median_warning_s.max() * 1.2))  # same scale as sweep_warning.png
    ax.set_xlim(left=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e4e4e2", lw=0.8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"graph: {path}")


def plot_bars(plt, res, path, col, scale, title, ylabel):
    full = largest(res)
    full = full.assign(**{col: full[col] * scale})
    bars = f"{col}_lo" in full  # error_bars.py adds <col>_lo / <col>_hi: the 95% range, drawn as a line through the bar
    windows = sorted(full.window_s.unique())
    models = [m for m in COLOURS if m in set(full.model)] + sorted(set(full.model) - set(COLOURS))
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    width = 0.8 / len(models)
    for j, m in enumerate(models):
        vals = [float(full[(full.model == m) & (full.window_s == w)][col].iloc[0]) for w in windows]
        xs = [i - 0.4 + width * (j + 0.5) for i in range(len(windows))]
        ax.bar(xs, vals, width * 0.92, color=COLOURS.get(m, "#888888"), label=NAMES.get(m, m))
        tops = vals
        if bars:
            lo, tops = ([float(full[(full.model == m) & (full.window_s == w)][f"{col}_{end}"].iloc[0]) * scale
                         for w in windows] for end in ("lo", "hi"))
            ax.vlines(xs, lo, tops, color="black", lw=1.2)
        for x, v in zip(xs, vals):
            if bars:  # the line's top end belongs to the range, so the value goes inside the foot of the bar
                ax.text(x, 2, f"{v:.0f}", ha="center", fontsize=7.5, fontweight="bold", color="white")
            else:
                ax.text(x, v + 1.5, f"{v:.0f}", ha="center", fontsize=11 if len(models) <= 2 else 9, fontweight="bold")
    ax.set_xticks(range(len(windows)), [label(w) for w in windows], fontsize=11)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_xlabel("Window (how far ahead the model looks)", fontsize=11)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_ylim(0, max(100, full[col].max() * 1.2, full[f"{col}_hi"].max() * scale * 1.1 if bars else 0))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e4e4e2", lw=0.8)
    ax.set_axisbelow(True)
    if bars:
        ax.text(0, -0.2, "Black lines: 95% range when the runs are re-drawn at random.", transform=ax.transAxes, fontsize=9)
    if len(models) > 2:  # too wide for one row above the bars: beside the graph, like plot_data
        ax.legend(frameon=False, fontsize=11, loc="center left", bbox_to_anchor=(1.01, 0.5))
    else:
        ax.legend(frameon=False, fontsize=11, loc="upper left", ncols=len(models))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"graph: {path}")


def show_numbers(res):
    """Print exactly what the graphs plot."""
    for col, title, scale in [("median_warning_s", "typical warning time (s)", 1),
                              ("alarms_real_share", "alarms that were real (%)", 100)]:
        print(f"\n== {title}; columns = runs trained on ==")
        t = res.assign(runs=res.train_runs.astype(str), value=res[col] * scale)
        t = t.pivot_table(index=["model", "window_s"], columns="runs", values="value")
        order = sorted(t.columns, key=lambda c: (not c.isdigit(), int(c) if c.isdigit() else 0))  # 12, 24, ..., all
        print(t[order].round(1).to_string())
        if f"{col}_lo" in res:
            print(f"\n== {title}: 95% range ==")
            print(res.assign(lo=res[f"{col}_lo"] * scale, hi=res[f"{col}_hi"] * scale, value=res[col] * scale)
                  .set_index(["model", "window_s"])[["lo", "value", "hi"]].round(1).to_string())


def selftest():
    assert label(30) == "30 s" and label(120) == "2 min" and label(600) == "10 min"
    res = pd.DataFrame({"model": "RandomForest", "window_s": [30, 30, 120, 120], "train_runs": [12, "all"] * 2,
                        "crush_runs": [3.0, 25.6, 3.2, 25.6], "alarms_real_share": [0.3, 0.3, 0.2, 0.33],
                        "median_warning_s": [25.0, 26.0, 45.0, 65.0]})
    full = largest(res)
    assert list(full.window_s) == [30, 120] and list(full.median_warning_s) == [26.0, 65.0]
    show_numbers(res)
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Draw readable graphs from a sweep.py results.csv.")
    p.add_argument("results", type=Path, help="results.csv written by sweep.py")
    p.add_argument("--models", default=None, help="only these models, comma-separated (default: every model in the csv)")
    args = p.parse_args()
    res = pd.read_csv(args.results)
    if args.models:
        res = res[res.model.isin(args.models.split(","))]
    show_numbers(res)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib is not installed (pip install matplotlib); the numbers above are what would be plotted")
    out = args.results.parent
    for model in res.model.unique():
        if res[res.model == model].train_runs.nunique() > 1:  # one training size has no line to draw
            plot_data(plt, res, model, out / f"sweep_data_{model}.png")
    for name, bar in BARS.items():
        plot_bars(plt, res, out / name, *bar)


if __name__ == "__main__":
    main()
