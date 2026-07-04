"""Serial filesystem job watcher for Intelligence Center Phase 1.

The watcher polls ``jobs/*/status.json``, picks pending work with Track B
priority, and invokes ``brief_router.py --job`` under a single-process file
lock. Production Linux uses ``fcntl.flock``; Windows development uses a small
best-effort ``msvcrt`` fallback.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time


try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


def log(message):
    """Print a UTF-8 timestamped log line."""
    print("%s %s" % (datetime.datetime.now().isoformat(timespec="seconds"), message), flush=True)


def project_root():
    """Return the project root inferred from this file location."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def jobs_dir(root_dir=None):
    """Return the jobs directory for a project root and ensure it exists."""
    root = os.path.abspath(root_dir or project_root())
    path = os.path.join(root, "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def read_json(path):
    """Read a UTF-8 JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    """Write pretty UTF-8 JSON."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def now_iso():
    """Return current local ISO timestamp without microseconds."""
    return datetime.datetime.now().replace(microsecond=0).isoformat()


class FileLock:
    """Cross-platform non-blocking lock around jobs/.lock."""

    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, "a+", encoding="utf-8")
        if fcntl:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.handle.close()
                self.handle = None
                return False
            return True
        if msvcrt:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.handle.close()
                self.handle = None
                return False
            return True
        # No locking mechanism available - fail loudly instead of silent degradation
        raise RuntimeError("No file locking mechanism available (fcntl or msvcrt required)")

    def __exit__(self, exc_type, exc, tb):
        if not self.handle:
            return
        if fcntl:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt:
            self.handle.seek(0)
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.handle.close()


def iter_jobs(root_dir=None):
    """Yield job directories that contain status.json."""
    base = jobs_dir(root_dir)
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "status.json")):
            yield path


def load_status(job_path):
    """Load status.json for a job directory."""
    return read_json(os.path.join(job_path, "status.json"))


def save_status(job_path, status):
    """Persist status.json for a job directory."""
    write_json(os.path.join(job_path, "status.json"), status)


def mark_retry_or_failed(job_path, status, error):
    """Increment retry count, then reset to pending or permanently fail."""
    retries = int(status.get("retry_count") or 0) + 1
    status["retry_count"] = retries
    status["error"] = error
    status["completed_at"] = now_iso()
    if retries < 2:
        status["state"] = "pending"
        status["started_at"] = None
        log("retry scheduled for %s retry_count=%d" % (status.get("job_id"), retries))
    else:
        status["state"] = "failed"
        log("job failed %s error=%s" % (status.get("job_id"), error))
    save_status(job_path, status)


def detect_timeouts(root_dir=None):
    """Mark stale running jobs as retryable or failed."""
    changed = 0
    now = time.time()
    for job_path in iter_jobs(root_dir):
        try:
            status = load_status(job_path)
        except (OSError, json.JSONDecodeError):
            continue
        if status.get("state") != "running" or not status.get("started_at"):
            continue
        try:
            started = datetime.datetime.fromisoformat(status["started_at"]).timestamp()
        except ValueError:
            continue
        timeout = int(status.get("timeout") or 3600)
        if now - started > timeout:
            mark_retry_or_failed(job_path, status, "timeout after %s seconds" % timeout)
            changed += 1
    return changed


def has_running_job(root_dir=None):
    """Return True if any job is currently running."""
    for job_path in iter_jobs(root_dir):
        try:
            if load_status(job_path).get("state") == "running":
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def job_track(job_path):
    """Read a job's track from job_spec.json, defaulting to Track A."""
    try:
        spec = read_json(os.path.join(job_path, "job_spec.json"))
        return spec.get("track", "A")
    except (OSError, json.JSONDecodeError):
        return "A"


def pick_pending_job(root_dir=None):
    """Pick the next pending job, preferring Track B over Track A."""
    pending = []
    for job_path in iter_jobs(root_dir):
        try:
            status = load_status(job_path)
        except (OSError, json.JSONDecodeError):
            continue
        if status.get("state") == "pending":
            pending.append((0 if job_track(job_path) == "B" else 1, os.path.basename(job_path), job_path))
    pending.sort()
    return pending[0][2] if pending else None


def process_job(job_path, router_path=None):
    """Run brief_router.py for one job and update status from running to terminal state."""
    router = router_path or os.path.join(os.path.dirname(__file__), "brief_router.py")
    status = load_status(job_path)
    status["state"] = "running"
    status["started_at"] = now_iso()
    status["completed_at"] = None
    status["error"] = None
    save_status(job_path, status)
    log("running job %s" % status.get("job_id"))
    proc = subprocess.run(
        [sys.executable, router, "--job", job_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=status.get("timeout", 3600),
    )
    status = load_status(job_path)
    if proc.returncode == 0:
        status["state"] = "done"
        status["completed_at"] = now_iso()
        status["error"] = None
        save_status(job_path, status)
        log("done job %s" % status.get("job_id"))
        return True
    error = (proc.stderr or proc.stdout or "brief_router failed").strip()
    mark_retry_or_failed(job_path, status, error)
    return False


def run_once(root_dir=None, router_path=None):
    """Run one watcher cycle and return a short result string for tests/CLI."""
    base = jobs_dir(root_dir)
    with FileLock(os.path.join(base, ".lock")) as locked:
        if not locked:
            log("lock busy")
            return "locked"
        detect_timeouts(root_dir)
        if has_running_job(root_dir):
            log("running job exists; waiting")
            return "running"
        job_path = pick_pending_job(root_dir)
        if not job_path:
            return "idle"
        process_job(job_path, router_path)
        return "processed"


def watch(root_dir=None, router_path=None, interval=30):
    """Poll forever, processing at most one job per cycle."""
    log("task watcher started interval=%s" % interval)
    while True:
        run_once(root_dir=root_dir, router_path=router_path)
        time.sleep(interval)


def main(argv=None):
    """CLI entrypoint for the serial job watcher."""
    parser = argparse.ArgumentParser(description="Watch jobs/ and process pending Intelligence Center jobs")
    parser.add_argument("--root", default=None, help="Project root; defaults to parent of src/")
    parser.add_argument("--router", default=None, help="Path to brief_router.py")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.once:
        return 0 if run_once(args.root, args.router) in ("processed", "idle", "running", "locked") else 1
    watch(args.root, args.router, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
