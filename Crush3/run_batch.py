#!/usr/bin/env python3
"""
run_batch.py

Unattended data collection. For each seed (start-seed, start-seed+1, ...):
  1. make_maps.py generates new maps + gen.json crowds (+ CrowdWalk randseed) for that seed
  2. every run is simulated in CUI mode (quickstart.sh <prop.json> -c -lError); each run's
     scenario.json ends it at --sim-minutes of simulated time (Finish event), and --timeout
     (wall-clock) kills it if it hangs anyway
  3. when a run finishes, its linkMetrics.csv is loaded into the DuckDB table via
     link_context.py (link context + map details per record) and the CSV is deleted
  4. on to the next seed -> new maps, new crowds, new randseed

Safe to stop (Ctrl-C) and restart with the same arguments: runs already in the DB
are skipped, an interrupted run is simply redone. A run that fails or exceeds
--timeout is logged and skipped; the batch keeps going. runner_log.csv (in --out)
has one row per attempted run: run id, status, seconds, rows loaded, message.

Run ids in the DB are seed_<s>/<map>/run_<j>, e.g. seed_3/funnel_002/run_1.
The per-agent log (agents.csv) is not in the DB; it is kept unless --drop-agent-log.

Usage (on the Linux machine, from crowdwalk/):
    python3 Crush3/run_batch.py                      # uses the SETTINGS block below
    python3 Crush3/run_batch.py --batches 5 --n 4 --runs-per-map 3 \\
        --out /mnt/ssd_2tb/gen_maps --log-root /mnt/ssd_2tb/gen_logs --db /mnt/ssd_2tb/crush.duckdb
    python3 Crush3/run_batch.py --runs 200 ...       # stop once 200 runs are in the DB (new seeds as needed)
    python3 Crush3/run_batch.py --batches 0 ...      # 0 = keep going until Ctrl-C
    python3 Crush3/run_batch.py --selftest           # uses a fake simulator, runs anywhere
"""

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from link_context import TABLE, build_run, read_json_c, write_db  # noqa: E402
from make_maps import FAMILIES as ALL_FAMILIES, generate  # noqa: E402

# =============================================================================
# SETTINGS -- edit these, then just run:  python3 Crush3/run_batch.py
# Any command-line option (e.g. --runs 20) overrides the value here for that run.
# =============================================================================

# How much to run
RUNS = None               # stop after this many runs are loaded into the DB (None = use BATCHES)
BATCHES = 1               # number of seeds to run when RUNS is None (0 = until Ctrl-C)
N_MAPS_PER_FAMILY = 4     # maps per family, per seed
RUNS_PER_MAP = 3          # different crowds (gen.json) per map
FAMILIES = ["corridor", "funnel", "tjunction", "crossroads", "tree"]
START_SEED = 0            # same seed -> same maps and crowds

# Where things go ("~" = your home folder)
OUT_DIR = "~/crush_test/maps"              # scenario folders, sim.log files, runner_log.csv
LOG_ROOT = None                            # simulator CSVs (None = inside each run folder under OUT_DIR)
DB_PATH = "/mnt/ssd_1tb/crush/crush.duckdb"  # the database (its folder must exist)

# Simulation
SIM_MINUTES = 60          # simulated minutes per run (scenario Finish event)
TIMEOUT = 3600            # real seconds before a stuck run is killed
JOBS = 1                  # simulations at the same time

# Output / housekeeping
PROGRESS_EVERY = 30       # seconds between progress lines (0 = off)
DROP_AGENT_LOG = False    # also delete agents.csv after loading
MAX_FAIL_STREAK = 10      # stop after this many failures in a row

# =============================================================================


def quickstart_cmd(crowdwalk_dir):
    return lambda prop: ["sh", str(crowdwalk_dir / "quickstart.sh"), str(prop), "-c", "-lError"]


def simulate(cmd, prop, cwd, timeout):
    """Run one simulation; returns (ok, message). Output goes to sim.log next to prop.json.
    Runs in its own process group so a timeout kills the JVM too, not just the sh wrapper."""
    env = {**os.environ, "LANG": os.environ.get("LANG", "ja_JP.UTF-8"),
           "JAVA_OPTS": os.environ.get("JAVA_OPTS", "-Dgroovy.source.encoding=UTF-8 -Dfile.encoding=UTF-8")}
    with open(prop.parent / "sim.log", "w") as log:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env,
                             start_new_session=(os.name == "posix"))
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL) if os.name == "posix" else p.kill()
            p.wait()
            return False, f"timeout after {timeout}s"
    return code == 0, f"exit code {code}"


def last_tick(link_csv):
    """Simulated second the run has reached = first column of the last complete line of its
    linkMetrics.csv (only the file's tail is read, so this is cheap even for big files)."""
    try:
        with open(link_csv, "rb") as f:
            f.seek(0, 2)
            start = max(0, f.tell() - 4096)
            f.seek(start)
            data = f.read().decode("utf-8", "ignore")
    except OSError:
        return None
    lines = data.split("\n")
    lines = lines[1 if start else 0:-1]  # drop the line cut by the seek, and the one still being written
    for line in reversed(lines):
        try:
            return int(float(line.split(",")[0]))
        except ValueError:  # header
            continue
    return None


def fmt_secs(secs):
    m, s = divmod(int(secs), 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m{s:02d}s"


def done_runs(db):
    if not Path(db).exists():
        return set()
    import duckdb
    with duckdb.connect(str(db), read_only=True) as con:
        if TABLE not in {r[0] for r in con.execute("SHOW TABLES").fetchall()}:
            return set()
        return {r[0] for r in con.table(TABLE).select("scenario_id").distinct().fetchall()}


def load_run(prop, run_id, db, drop_agent_log):
    """Finished run -> DB. Returns (rows loaded, message); deletes linkMetrics.csv once it is in the DB."""
    pj = read_json_c(prop)
    link_csv = Path(pj["link_logging"]["file"])
    if not link_csv.exists() or link_csv.stat().st_size == 0:
        return 0, f"no linkMetrics.csv at {link_csv}"
    df = pd.read_csv(link_csv)
    if df.empty:
        return 0, "linkMetrics.csv has no rows"
    write_db(build_run(df, prop.parent / pj["map_file"], run_id), db, run_id)
    link_csv.unlink()
    agent_file = pj.get("dynamic_logging", {}).get("file")  # per-agent logger is optional
    if drop_agent_log and agent_file and Path(agent_file).exists():
        Path(agent_file).unlink()
    return len(df), ""


def run_batches(args, sim_cmd):
    out, log_path = Path(args.out), Path(args.out) / "runner_log.csv"
    out.mkdir(parents=True, exist_ok=True)
    new_log = not log_path.exists()
    log_f = open(log_path, "a", newline="", encoding="utf-8")
    log = csv.writer(log_f)
    if new_log:
        log.writerow(["run_id", "status", "seconds", "rows", "message"])
    done = done_runs(args.db)
    seed, batch, loaded, fail_streak = args.start_seed, 0, 0, 0

    running, lock, stop = {}, threading.Lock(), threading.Event()
    sim_secs = args.sim_minutes * 60

    def timed_sim(prop, run_id):
        t0 = time.time()
        with lock:
            running[run_id] = (t0, Path(read_json_c(prop)["link_logging"]["file"]))
        print(f"  started {run_id}", flush=True)
        try:
            ok, msg = simulate(sim_cmd(prop), prop, args.crowdwalk, args.timeout)
        finally:
            with lock:
                running.pop(run_id, None)
        return ok, msg, time.time() - t0

    def heartbeat():  # every --progress-every seconds: one line per run in progress
        while not stop.wait(args.progress_every):
            with lock:
                items = list(running.items())
            for run_id, (t0, link_csv) in items:
                tick = last_tick(link_csv)
                where = f"simulated {tick}/{sim_secs} s ({100 * tick / sim_secs:.0f}%)" if tick is not None \
                    else "starting up (no link log yet)"
                print(f"    ... {run_id}: running {fmt_secs(time.time() - t0)}, {where}", flush=True)

    if args.progress_every:
        threading.Thread(target=heartbeat, daemon=True).start()

    try:
        while (loaded < args.runs) if args.runs else (args.batches == 0 or batch < args.batches):
            tag = f"seed_{seed}"
            rows = generate(out / tag, args.n, seed, HERE, Path(args.log_root) / tag if args.log_root else None,
                            args.families, args.runs_per_map, args.sim_minutes)
            todo = [r for r in rows if f"{tag}/{r['run']}" not in done]
            if args.runs:
                todo = todo[:args.runs - loaded]  # only launch what is still needed
            print(f"[{tag}] {len(rows)} runs generated, {len(rows) - len(todo)} already in DB or not needed, "
                  f"{len(todo)} to simulate", flush=True)
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = {pool.submit(timed_sim, out / tag / r["run"] / "prop.json", f"{tag}/{r['run']}"):
                           (f"{tag}/{r['run']}", out / tag / r["run"] / "prop.json") for r in todo}
                for i, fut in enumerate(as_completed(futures), 1):  # DB writes stay in this thread (single writer)
                    run_id, prop = futures[fut]
                    ok, msg, secs = fut.result()
                    n_rows = 0
                    if ok:
                        n_rows, msg = load_run(prop, run_id, args.db, args.drop_agent_log)
                    status = "loaded" if n_rows else "failed"
                    if n_rows:
                        loaded, fail_streak = loaded + 1, 0
                        done.add(run_id)
                    else:
                        fail_streak += 1
                    log.writerow([run_id, status, round(secs, 1), n_rows, msg])
                    log_f.flush()
                    print(f"  [{i}/{len(todo)}] {run_id}: {status} ({fmt_secs(secs)}{', ' + msg if msg else ''})",
                          flush=True)
            if fail_streak >= args.max_fail_streak:  # broken setup, not one bad map -> don't loop forever
                print(f"{fail_streak} runs failed in a row -- stopping; check {out}/runner_log.csv and a sim.log")
                break
            seed, batch = seed + 1, batch + 1
    except KeyboardInterrupt:
        print("\nstopped -- finished runs are kept in the DB; rerunning skips them")
    finally:
        stop.set()
        log_f.close()
        print(f"loaded {loaded} new run(s) into {args.db}" + (f" (target {args.runs})" if args.runs else ""))
    return loaded


def selftest():
    """Fake simulator: writes a small linkMetrics.csv for the run's map; fails on purpose for one run."""
    tmp = Path(tempfile.mkdtemp())
    fake = tmp / "fake_sim.py"
    fake.write_text(
        "import sys, random\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "from link_context import read_json_c, parse_links\n"
        "prop = Path(sys.argv[1]); pj = read_json_c(prop)\n"
        "if 'tjunction_000/run_1' in prop.as_posix(): sys.exit(3)\n"
        "ids = parse_links(prop.parent / pj['map_file']).link_id\n"
        "out = Path(pj['link_logging']['file']); out.parent.mkdir(parents=True, exist_ok=True)\n"
        "rows = ['current_traveling_period,link_id,agent_count,mean_speed,max_compression_pressure']\n"
        "rows += [f'{t},{l},{random.randint(1,20)},0.8,{random.uniform(0,1500):.1f}' for t in range(20) for l in ids]\n"
        "out.write_text('\\n'.join(rows) + '\\n')\n"
        "if 'dynamic_logging' in pj: Path(pj['dynamic_logging']['file']).write_text('x\\n')\n", encoding="utf-8")
    sim = lambda prop: [sys.executable, str(fake), str(prop)]
    args = argparse.Namespace(out=str(tmp / "maps"), log_root=str(tmp / "logs"), db=str(tmp / "t.duckdb"),
                              start_seed=5, batches=2, runs=None, n=1, runs_per_map=2, sim_minutes=60, families=ALL_FAMILIES, jobs=2,
                              timeout=60, crowdwalk=tmp, drop_agent_log=False,
                              max_fail_streak=10, progress_every=1)
    run_batches(args, sim)
    import duckdb
    with duckdb.connect(args.db, read_only=True) as con:
        runs = {r[0] for r in con.table(TABLE).select("scenario_id").distinct().fetchall()}
        maps = len(con.table(TABLE).select("map_id").distinct().fetchall())
    log = pd.read_csv(tmp / "maps" / "runner_log.csv")
    # 2 seeds x 5 families x 1 map x 2 runs = 20 runs; the forced failure is logged, not loaded
    assert len(log) == 20 and (log.status == "failed").sum() == 2, log  # tjunction_000/run_1 in both seeds
    assert len(runs) == 18 and "seed_5/corridor_000/run_0" in runs and "seed_6/tree_000/run_1" in runs
    assert maps == 10  # new seed -> new maps
    assert not list((tmp / "logs").rglob("linkMetrics.csv"))  # moved into the DB
    assert len(list((tmp / "logs").rglob("agents.csv"))) == 18  # kept (no --drop-agent-log)
    # restart with the same arguments: only the 2 failures are retried
    run_batches(args, sim)
    log = pd.read_csv(tmp / "maps" / "runner_log.csv")
    assert len(log) == 22 and len(done_runs(args.db)) == 18
    # --runs: exactly N new runs loaded, crossing into new seeds as needed; failures don't count
    args2 = argparse.Namespace(**{**vars(args), "db": str(tmp / "r.duckdb"), "out": str(tmp / "maps_r"),
                                  "log_root": str(tmp / "logs_r"), "batches": 1, "runs": 7})
    assert run_batches(args2, sim) == 7 and len(done_runs(args2.db)) == 7
    args2.runs = 12  # seed_5 has 3 left (1 always fails) -> 2, then 10 more from seed_6 and seed_7
    assert run_batches(args2, sim) == 12 and len(done_runs(args2.db)) == 19
    assert {r.split("/")[0] for r in done_runs(args2.db)} == {"seed_5", "seed_6", "seed_7"}
    # a setup where everything fails stops after --max-fail-streak failures instead of looping forever
    args3 = argparse.Namespace(**{**vars(args2), "db": str(tmp / "f.duckdb"), "out": str(tmp / "maps_f"), "runs": 5})
    assert run_batches(args3, lambda prop: [sys.executable, "-c", "raise SystemExit(1)"]) == 0
    assert len(pd.read_csv(tmp / "maps_f" / "runner_log.csv")) == 10
    # link logger only (no dynamic_logging in prop.json): loading still works, even with --drop-agent-log
    lo = tmp / "linkonly"
    lo.mkdir()
    (lo / "linkMetrics.csv").write_text("current_traveling_period,link_id,agent_count,mean_speed,max_compression_pressure\n"
                                        "0,_p00010,5,0.8,100.0\n", encoding="utf-8")
    (lo / "prop.json").write_text(json.dumps({"map_file": "Map.xml", "link_logging": {"file": str(lo / "linkMetrics.csv")}}),
                                  encoding="utf-8")
    (lo / "Map.xml").write_text((HERE / "Map.xml").read_text(encoding="utf-8"), encoding="utf-8")
    assert load_run(lo / "prop.json", "linkonly", str(tmp / "lo.duckdb"), drop_agent_log=True) == (1, "")
    # progress: last_tick reads the last complete line, skipping a half-written one
    pt = tmp / "progress.csv"
    pt.write_text("current_traveling_period,link_id\n" + "".join(f"{t},_p1\n" for t in range(2000)) + "20", encoding="utf-8")
    assert last_tick(pt) == 1999 and last_tick(tmp / "missing.csv") is None  # not the half-written "20"
    pt.write_text("current_traveling_period,link_id\n" + "".join(f"{t},_p1\n" for t in range(5)), encoding="utf-8")
    assert last_tick(pt) == 4  # small file: read from the start, header skipped
    pt.write_text("current_traveling_period,link_id\n", encoding="utf-8")
    assert last_tick(pt) is None
    assert fmt_secs(65) == "1m05s" and fmt_secs(3725) == "1h02m"
    # a run over --timeout is killed and reported, not waited on
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    t0 = time.time()
    ok, msg = simulate(slow, tmp / "maps" / "seed_5" / "corridor_000" / "run_0" / "prop.json", tmp, 1)
    assert not ok and "timeout" in msg and time.time() - t0 < 10
    print("selftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Generate -> simulate -> load into DuckDB, seed after seed.")
    # defaults come from the SETTINGS block at the top of this file
    p = argparse.ArgumentParser(description="Generate -> simulate -> load into DuckDB, seed after seed. "
                                            "Defaults come from the SETTINGS block at the top of this file.")
    p.add_argument("--runs", type=int, default=RUNS,
                   help="stop after this many runs are loaded into the DB (failed runs don't count); "
                        "moves on to new seeds as needed and overrides --batches")
    p.add_argument("--batches", type=int, default=BATCHES, help="number of seeds to run (0 = until Ctrl-C)")
    p.add_argument("--start-seed", type=int, default=START_SEED)
    p.add_argument("--n", type=int, default=N_MAPS_PER_FAMILY, help="maps per family per seed")
    p.add_argument("--runs-per-map", type=int, default=RUNS_PER_MAP)
    p.add_argument("--sim-minutes", type=int, default=SIM_MINUTES, help="simulated minutes before Finish")
    p.add_argument("--families", nargs="+", choices=ALL_FAMILIES, default=FAMILIES)
    p.add_argument("--out", default=OUT_DIR, help="where scenario folders + runner_log.csv go")
    p.add_argument("--log-root", default=LOG_ROOT, help="where the simulator writes CSVs (default: each run folder)")
    p.add_argument("--db", default=DB_PATH)
    p.add_argument("--jobs", type=int, default=JOBS, help="simulations in parallel")
    p.add_argument("--timeout", type=int, default=TIMEOUT, help="seconds before a run is killed")
    p.add_argument("--crowdwalk", type=Path, default=HERE.parent, help="folder with quickstart.sh (default: crowdwalk/)")
    p.add_argument("--drop-agent-log", action=argparse.BooleanOptionalAction, default=DROP_AGENT_LOG,
                   help="also delete agents.csv after loading")
    p.add_argument("--progress-every", type=int, default=PROGRESS_EVERY,
                   help="seconds between progress lines for running simulations (0 = off)")
    p.add_argument("--max-fail-streak", type=int, default=MAX_FAIL_STREAK,
                   help="stop after this many failed runs in a row -- a broken setup, not a bad map")
    args = p.parse_args()
    args.crowdwalk = args.crowdwalk.resolve()
    # "~" -> home folder, so the SETTINGS paths can use it
    args.out = str(Path(args.out).expanduser())
    args.db = str(Path(args.db).expanduser())
    args.log_root = str(Path(args.log_root).expanduser()) if args.log_root else None
    if not Path(args.db).parent.exists():
        sys.exit(f"database folder {Path(args.db).parent} does not exist -- create it or change DB_PATH")
    print(f"runs: {args.runs if args.runs else f'{args.batches} seed(s)'}, {args.n} map(s) x "
          f"{len(args.families)} famil{'y' if len(args.families) == 1 else 'ies'} x {args.runs_per_map} run(s) per seed\n"
          f"out: {args.out}\nlogs: {args.log_root or '(inside each run folder)'}\ndb: {args.db}", flush=True)
    run_batches(args, quickstart_cmd(args.crowdwalk))


if __name__ == "__main__":
    main()
