#!/usr/bin/env python3
"""
compare_plot.py

Bar graphs that compare finished force_model.py tests side by side (e.g. seen maps, unseen
maps, random networks). It only reads each test's accuracy.csv and crossings.csv and draws
them: no model is run and nothing is recomputed. The numbers it plots are printed first.

One graph per measure:
  compare_mae.png       average error (MAE, N) of the predicted peak
  compare_warning.png   typical warning before an event (median_warning_s, s)
  compare_alarms.png    alarms that were real (alarms_real_share, %)

Usage (each argument is label=folder, the folder holding accuracy.csv and crossings.csv):
    python3 compare_plot.py "Seen maps=R/2026-10-28_fm_r2a_v2_w120/h120_runsall" \
        "Unseen maps=R/2026-10-30_fm_r3_maps5_v2_w120/pressure" \
        "Random networks=R/2026-10-31_fm_r3_randnet_v2_w120/pressure" --out-dir fm_compare_r3
    python3 compare_plot.py --selftest
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# same colours and names as sweep_plot.py
COLOURS = {"baseline: now": "#999999", "Holt": "#009E73", "RF_history": "#08306b", "XGB_history": "#a66a00"}
NAMES = {"baseline: now": "No-change baseline", "RF_history": "RandomForest + history",
         "XGB_history": "XGBoost + history"}
# file name -> (column, multiplier, title, y-axis label, unit)
GRAPHS = {"compare_mae.png": ("MAE", 1, "Average error of the predicted 2 min peak", "Average error, MAE (N)", "N"),
          "compare_warning.png": ("median_warning_s", 1, "Warning before an event", "Typical warning (s)", "s"),
          "compare_alarms.png": ("alarms_real_share", 100, "Alarms that were real", "Alarms that were real (%)", "%")}


def load(folder):
    """One row per model: MAE (mean over folds) joined with the crossings table."""
    acc = pd.read_csv(Path(folder) / "accuracy.csv", header=[0, 1], index_col=0)
    acc = pd.DataFrame({"MAE": acc[("MAE", "mean")], "MAE_fold_sd": acc[("MAE", "std")]})
    acc.index.name = None
    cr = pd.read_csv(Path(folder) / "crossings.csv", index_col=0)
    return acc.join(cr, how="left")


def table(tests):
    """Long table: test, model and every measure, only the models the graphs draw."""
    rows = [t.assign(test=name, model=t.index) for name, t in tests.items()]
    res = pd.concat(rows, ignore_index=True)
    return res[res.model.isin(COLOURS)]


def show_numbers(res):
    print("== numbers the graphs plot ==")
    t = res.assign(alarms_real_pct=res.alarms_real_share * 100,
                   events_warned=res.events_warned.map(lambda v: "" if pd.isna(v) else f"{v:.0f}") + " of "
                   + res.events.map(lambda v: "" if pd.isna(v) else f"{v:.0f}"))
    cols = ["test", "model", "MAE", "MAE_fold_sd", "median_warning_s", "alarms_real_pct", "events_warned", "alarms"]
    print(t[cols].round(1).to_string(index=False))


def plot(plt, res, path, col, scale, title, ylabel, unit):
    tests = list(dict.fromkeys(res.test))
    models = [m for m in COLOURS if m in set(res.dropna(subset=[col]).model)]
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    width = 0.8 / len(models)
    top = 0
    for j, m in enumerate(models):
        for i, name in enumerate(tests):
            v = res[(res.model == m) & (res.test == name)][col]
            if v.empty or pd.isna(v.iloc[0]):
                continue  # e.g. no crossings row for this model in that test
            v = float(v.iloc[0]) * scale
            x = i - 0.4 + width * (j + 0.5)
            ax.bar(x, v, width * 0.92, color=COLOURS[m], label=NAMES.get(m, m) if i == 0 else None)
            ax.text(x, v * 1.01 + 1, f"{v:.0f}", ha="center", va="bottom", fontsize=11, fontweight="bold")
            top = max(top, v)
    ax.set_xticks(range(len(tests)), tests, fontsize=12)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_ylim(0, 100 if unit == "%" else top * 1.15)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=11, loc="center left", bbox_to_anchor=(1.01, 0.5))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"graph: {path}")


def selftest():
    acc = pd.DataFrame({("MAE", "mean"): [180.0, 80.0, 70.0], ("MAE", "std"): [20.0, 10.0, 9.0]},
                       index=["baseline: now", "RF_history", "Ridge"])
    cr = pd.DataFrame({"alarms_real_share": [0.75], "median_warning_s": [115.0], "events": [10],
                       "events_warned": [9.0], "alarms": [40]}, index=["RF_history"])
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        acc.to_csv(Path(d) / "accuracy.csv")
        cr.to_csv(Path(d) / "crossings.csv")
        res = table({"A": load(d)})
    assert list(res.model) == ["baseline: now", "RF_history"]  # Ridge is not drawn
    assert res.set_index("model").loc["RF_history", "MAE"] == 80.0
    assert pd.isna(res.set_index("model").loc["baseline: now", "alarms_real_share"])
    show_numbers(res)
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Compare finished tests side by side, one bar graph per measure.")
    p.add_argument("tests", nargs="+", help='label=folder, e.g. "Unseen maps=.../pressure"')
    p.add_argument("--out-dir", type=Path, default=Path("fm_compare"))
    a = p.parse_args()
    tests = {}
    for t in a.tests:
        name, _, folder = t.partition("=")
        if not folder:
            p.error(f"expected label=folder, got {t!r}")
        tests[name] = load(folder)
    res = table(tests)
    show_numbers(res)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for fname, spec in GRAPHS.items():
        plot(plt, res, a.out_dir / fname, *spec)


if __name__ == "__main__":
    main()
