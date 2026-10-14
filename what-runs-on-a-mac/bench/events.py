"""Event writer (harness -> Bench Monitor). Append-only JSON lines; the app tails them.

Each run gets runs/<run_id>/ with events.jsonl; runs/current is a symlink to the active run.
run_end / run_fail lines are also appended to runs/history.jsonl for the app's results table.
"""
import datetime, json, os
from pathlib import Path
from . import ROOT

RUNS = ROOT / "runs"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventWriter:
    def __init__(self, run_id):
        self.run_id = run_id
        self.dir = RUNS / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"
        cur = RUNS / "current"
        tmp = RUNS / ".current.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(self.dir.name)
        os.replace(tmp, cur)          # atomic repoint

    def emit(self, type_, **fields):
        rec = {**fields, "t": now(), "type": type_}     # t/type last: forwarded subprocess fields can't forge them
        line = json.dumps(rec) + "\n"
        with open(self.path, "a") as f:
            f.write(line)
        if type_ in ("run_end", "run_fail"):
            with open(RUNS / "history.jsonl", "a") as f:
                f.write(line)
        return rec

    def runner_pid(self, pid):
        (self.dir / "runner.pid").write_text(str(pid) if pid else "")


_LOCK = None


def acquire_bench_lock(what):
    """One measurement at a time: the LLM harness and the media runner share this lock, so two jobs
    can never share the GPU while timed (learned 2026-10-11 when two dry runs overlapped)."""
    import fcntl, sys
    global _LOCK
    RUNS.mkdir(parents=True, exist_ok=True)
    _LOCK = open(RUNS / ".bench.lock", "a+")
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _LOCK.seek(0)
        sys.exit(f"Another benchmark job holds runs/.bench.lock ({_LOCK.read().strip()}). One measurement at a time.")
    _LOCK.seek(0); _LOCK.truncate(); _LOCK.write(f"{what} pid {os.getpid()} since {now()}"); _LOCK.flush()
