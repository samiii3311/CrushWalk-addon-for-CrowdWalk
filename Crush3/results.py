#!/usr/bin/env python3
"""
results.py

Where results go: one folder per run under ROOT, with the date in front of the name, so the
folders sort in the order they were made and stay out of the source tree.

    --out-dir fm_h120      ->  <ROOT>/2026-10-12_fm_h120       (a plain name: dated, under ROOT)
    --out-dir out/fm_h120  ->  out/fm_h120                     (anything with a folder in it: used as given)
    --out-dir /tmp/x       ->  /tmp/x
    --out-dir 2026-10-10_fm_sweep  ->  <ROOT>/2026-10-10_fm_sweep   (already dated: that exact folder)

The same name on a later day makes a new folder; on the same day it is the same folder.

Reading works with the short name too: fm_h120/pressure/importance.csv is looked up under ROOT,
newest date first, when there is no such path where you are. The path that was picked is printed.

The scripts that finish a result (force_model.py, sweep.py, sweep_plot.py) also write
<folder>/<folder name>.zip with its graphs and tables, so a result can be fetched as one file.

    python3 results.py --selftest
"""

import os
import re
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

# =============================================================================
# SETTING -- the folder all results go in. Change it here, or set CRUSH_RESULTS for one terminal.
ROOT = Path(os.environ.get("CRUSH_RESULTS", "/mnt/ssd_1tb/crush/results"))
# =============================================================================

DATED = re.compile(r"\d{4}-\d{2}-\d{2}_")


def plain(path):
    """A single name with no folder in it: fm_h120 (and ./fm_h120, which Path reads as the same), but not a/b or /tmp/x."""
    return not path.is_absolute() and len(path.parts) == 1 and path.parts[0] not in (".", "..")


def new_dir(name):
    """The folder a new run writes to (see the notes at the top). Does not create it."""
    path = Path(name).expanduser()
    if not plain(path):
        return path
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.exit(f"cannot use the results folder {ROOT} ({e}).\n"
                 f"Change ROOT in {Path(__file__).name}, set CRUSH_RESULTS, or give a path such as --out-dir out/{path}")
    if DATED.match(path.name):
        return ROOT / path.name
    # the background copy of a job inherits the date its starter used, also across midnight
    day = os.environ.setdefault("CRUSH_RUN_DATE", date.today().isoformat())
    return ROOT / f"{day}_{path.name}"


def find(path):
    """An existing result, for reading: the path as given if it exists, otherwise the same path under ROOT
    with the newest date. Returns the path unchanged if nothing is found, so the caller's own error shows."""
    path = Path(path).expanduser()
    if path.exists() or path.is_absolute() or not path.parts or not ROOT.is_dir():
        return path
    first, rest = path.parts[0], path.parts[1:]
    folders = [ROOT / first] + sorted(ROOT.glob(f"????-??-??_{first}"), reverse=True)  # exact name, then newest first
    for folder in folders:
        hit = folder.joinpath(*rest)
        if hit.exists():
            print(f"using {hit}")
            return hit
    return path


def zip_up(folder):
    """<folder>/<folder name>.zip with every graph and table in the folder, to fetch a result as one file.
    The per-row predictions.csv files are left out: they are large and only error_bars.py needs them."""
    folder = Path(folder)
    out = folder / f"{folder.resolve().name}.zip"
    files = [f for f in sorted(folder.rglob("*")) if f.suffix in (".png", ".csv") and f.name != "predictions.csv"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(folder))
    print(f"zip: {out} ({len(files)} files)")


def selftest():
    global ROOT
    ROOT = Path(tempfile.mkdtemp()) / "results"
    os.environ["CRUSH_RUN_DATE"] = "2026-10-12"
    assert new_dir("fm_x") == ROOT / "2026-10-12_fm_x" and ROOT.is_dir()
    assert new_dir("2026-10-10_fm_x") == ROOT / "2026-10-10_fm_x"  # already dated: no second date
    assert new_dir("./fm_x") == ROOT / "2026-10-12_fm_x"  # Path drops the ./, so it is a plain name
    for given in ("a/fm_x", "/tmp/fm_x", "."):
        assert new_dir(given) == Path(given)  # anything with a folder in it is used as given
    for day in ("2026-10-10", "2026-10-12"):
        (ROOT / f"{day}_fm_x").mkdir()
        (ROOT / f"{day}_fm_x" / "results.csv").write_text(day)
    (ROOT / "2026-10-13_fm_x_other").mkdir()  # a longer name must not be mistaken for fm_x
    assert find("fm_x/results.csv").read_text() == "2026-10-12"  # the newest
    assert find("fm_x") == ROOT / "2026-10-12_fm_x"
    assert find("2026-10-10_fm_x/results.csv").read_text() == "2026-10-10"  # an exact folder name
    assert find("nothing/here.csv") == Path("nothing/here.csv")
    assert find(ROOT / "2026-10-10_fm_x") == ROOT / "2026-10-10_fm_x"
    run = ROOT / "2026-10-12_fm_x"
    (run / "h30_runsall").mkdir()
    for name in ("sweep.png", "run.log", "h30_runsall/accuracy.csv", "h30_runsall/predictions.csv"):
        (run / name).write_text("x")
    zip_up(run)
    zip_up(run)  # a second time replaces the zip and does not pack the old one into it
    assert sorted(zipfile.ZipFile(run / "2026-10-12_fm_x.zip").namelist()) == ["h30_runsall/accuracy.csv", "results.csv", "sweep.png"]
    print("selftest ok")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["--selftest"] else sys.exit(__doc__)
