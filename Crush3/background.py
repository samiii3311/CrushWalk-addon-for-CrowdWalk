#!/usr/bin/env python3
"""
background.py

Lets a long job outlive the terminal. run_batch.py, force_model.py and sweep.py use it: by
default they start themselves in the background, write everything to a log file, and show that
log live in the terminal.

  Ctrl-C                      stops showing the log. The job keeps running, also after the
                              terminal is closed.
  tail -f LOG                 shows the log again.
  <script> --stop --log LOG   ends the job, the same way Ctrl-C would in the foreground.
  <script> ... --foreground   the old behaviour: no log file, Ctrl-C ends the job.
  <script> ... --log FILE     put the log somewhere else (each script has its own default).

One job per log file: a second start with the same log is refused while the first is running.
On Windows everything runs in the foreground.

    python3 background.py --selftest
"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHILD = "CRUSH_BACKGROUND_CHILD"  # set in the background copy's environment
STOP_WAIT = 60  # seconds --stop waits for a clean exit before it kills the job


def add_args(p):
    g = p.add_argument_group("running in the background (the default)")
    g.add_argument("--log", type=Path, default=None, help="log file of the background job (default: see the script's notes)")
    g.add_argument("--stop", action="store_true", help="with --log LOG: end the background job that writes to LOG, then exit")
    g.add_argument("--foreground", action="store_true", help="run in this terminal instead: no log file, Ctrl-C ends the job")


def pid_file(log):
    return log.with_name(log.name + ".pid")


def running(log):
    """pid of the job writing to this log, or None."""
    try:
        pid = int(pid_file(log).read_text())
        os.kill(pid, 0)  # signal 0 = only check that the process exists
        return pid
    except (OSError, ValueError):
        return None


def stop(log):
    pid = running(log)
    if pid is None:
        pid_file(log).unlink(missing_ok=True)
        sys.exit(f"no job is running for {log}")
    os.kill(pid, signal.SIGINT)  # what Ctrl-C sends: the job stops its own work and says so in the log
    for _ in range(STOP_WAIT * 2):
        if running(log) is None:
            break
        time.sleep(0.5)
    else:
        os.killpg(pid, signal.SIGKILL)  # the job leads its own process group (start_new_session)
        print(f"the job did not stop within {STOP_WAIT} s and was killed")
    pid_file(log).unlink(missing_ok=True)
    print(f"stopped the job (pid {pid}); its output is in {log}")
    sys.exit(0)


def stop_requested():
    """Call first in main(): `<script> --stop --log LOG` works without the script's other arguments."""
    if "--stop" in sys.argv:
        p = argparse.ArgumentParser()
        p.add_argument("--log", type=Path, required=True)
        stop(p.parse_known_args()[0].log.expanduser())


def start(args, default_log):
    """Call right after parse_args(). Returns in the copy that does the work; the copy that only shows
    the log never returns from here."""
    log = Path(args.log or default_log).expanduser()
    if os.environ.get(CHILD):  # this is the background copy: leave a pid for --stop, then do the work
        pid_file(log).write_text(str(os.getpid()))
        atexit.register(lambda: pid_file(log).unlink(missing_ok=True))
        return
    if args.foreground or os.name != "posix":
        return
    if running(log):
        sys.exit(f"a job is already running with this log (pid {running(log)}).\n"
                 f"  show its log: tail -f {log}\n  end it:       {stop_command(log)}\n"
                 f"  or start this one with another --log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as f:
        seen = f.tell()  # an earlier job's output stays in the file; only show what is new
        child = subprocess.Popen([sys.executable, *sys.argv], stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,  # own session: survives Ctrl-C and a closed terminal
                                 env={**os.environ, CHILD: "1", "PYTHONUNBUFFERED": "1"})
    print(f"running in the background (pid {child.pid}), log: {log}\n"
          f"  Ctrl-C stops showing the log; the job keeps running\n  end the job: {stop_command(log)}\n", flush=True)
    try:
        with open(log, "rb") as f:
            f.seek(seen)
            while True:
                done = child.poll() is not None
                chunk = f.read()
                sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
                if done and not chunk:
                    break
                if not chunk:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\nno longer showing the log; the job is still running (pid {child.pid}).\n"
              f"  show it again: tail -f {log}\n  end the job:   {stop_command(log)}")
        sys.exit(0)
    sys.exit(child.returncode)


def stop_command(log):
    return f"python3 {sys.argv[0]} --stop --log {log}"


def selftest():
    if os.name != "posix":
        return print("background.py only backgrounds on Linux; nothing to test here")
    me = [sys.executable, __file__]
    log = Path(tempfile.mkdtemp()) / "job.log"

    # a short job: its output is shown and logged, and the pid file is gone afterwards
    r = subprocess.run(me + ["--demo", "2", "--log", str(log)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "tick 1" in r.stdout and "tick 1" in log.read_text(), r
    assert not pid_file(log).exists()

    # a long job: the viewer goes away on Ctrl-C, the job keeps running, a second start is refused, --stop ends it
    viewer = subprocess.Popen(me + ["--demo", "600", "--log", str(log)], stdout=subprocess.PIPE, text=True)
    for _ in range(100):
        if running(log):
            break
        time.sleep(0.1)
    pid = running(log)
    assert pid and pid != viewer.pid
    viewer.send_signal(signal.SIGINT)
    assert viewer.wait(timeout=10) == 0 and "still running" in viewer.stdout.read()
    time.sleep(1)
    assert running(log) == pid  # survived the viewer
    again = subprocess.run(me + ["--demo", "1", "--log", str(log)], capture_output=True, text=True, timeout=60)
    assert again.returncode != 0 and "already running" in again.stderr
    done = subprocess.run(me + ["--stop", "--log", str(log)], capture_output=True, text=True, timeout=STOP_WAIT + 30)
    assert done.returncode == 0 and "stopped the job" in done.stdout, done
    assert running(log) is None and not pid_file(log).exists()
    print("selftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    stop_requested()
    p = argparse.ArgumentParser(description="Background helper for the long-running scripts; see the notes at the top.")
    p.add_argument("--demo", type=int, default=None, metavar="SECONDS", help="a stand-in job that prints one line a second")
    add_args(p)
    args = p.parse_args()
    if args.demo is None:
        sys.exit(__doc__)
    start(args, "background_demo.log")
    for i in range(args.demo):
        print(f"tick {i}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
