"""Cron scheduler - background daemon thread for scheduled tasks.

Four-layer architecture adapted from Claude Code's cron system:
  1. Scheduler: daemon thread polls every 1s, fires matching jobs
  2. Queue: thread-safe queue decouples scheduler from consumer
  3. Consumer: Agent.chat() consumes queue at start of each turn
  4. Durable storage: .corecoder/cron_jobs.json survives restart
"""

import json
import random
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path


@dataclass
class CronJob:
    id: str
    cron: str        # "0 9 * * *"
    prompt: str      # message to inject when fired
    recurring: bool  # True = recurring, False = one-shot
    durable: bool    # True = persist to disk


class CronScheduler:
    """Manages cron jobs with a background daemon thread.

    Thread-safe: all public methods acquire _lock internally.
    """

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self._jobs: dict[str, CronJob] = {}
        self._queue: list[CronJob] = []
        self._lock = threading.RLock()
        self._last_fired: dict[str, str] = {}  # job_id -> "YYYY-MM-DD HH:MM"
        self._running = True
        self._on_fire = None  # optional callback(job) for notification

        self._load_durable()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()

    # -- Public API --------------------------------------------------

    def schedule(self, cron: str, prompt: str, recurring: bool = True,
                 durable: bool = True) -> CronJob | str:
        """Register a cron job. Returns CronJob or error string."""
        err = self._validate_cron(cron)
        if err:
            return err

        job = CronJob(
            id=f"cron_{random.randint(0, 999999):06d}",
            cron=cron, prompt=prompt,
            recurring=recurring, durable=durable,
        )
        with self._lock:
            self._jobs[job.id] = job
        if durable:
            self._save_durable()
        return job

    def cancel(self, job_id: str) -> str:
        """Cancel a cron job by ID."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if not job:
            return f"Job {job_id} not found"
        if job.durable:
            self._save_durable()
        return f"Cancelled {job_id}"

    def list_jobs(self) -> list[CronJob]:
        """Return a snapshot of all registered jobs."""
        with self._lock:
            return list(self._jobs.values())

    def consume_queue(self) -> list[CronJob]:
        """Consume all fired jobs from the queue."""
        with self._lock:
            fired = list(self._queue)
            self._queue.clear()
        return fired

    def has_pending(self) -> bool:
        """Return whether fired cron jobs are waiting to be delivered."""
        with self._lock:
            return bool(self._queue)

    def shutdown(self):
        """Stop the scheduler thread."""
        self._running = False

    # -- Internal: scheduler loop -----------------------------------

    def _scheduler_loop(self):
        """Daemon thread: poll every 1s, fire matching jobs."""
        while self._running:
            time.sleep(1)
            now = datetime.now()
            minute_marker = now.strftime("%Y-%m-%d %H:%M")

            with self._lock:
                for job in list(self._jobs.values()):
                    try:
                        if self._cron_matches(job.cron, now):
                            if self._last_fired.get(job.id) != minute_marker:
                                self._queue.append(job)
                                self._last_fired[job.id] = minute_marker
                                if self._on_fire:
                                    self._on_fire(job)
                            if not job.recurring:
                                self._jobs.pop(job.id, None)
                                if job.durable:
                                    self._save_durable()
                    except Exception:
                        pass  # one bad job shouldn't kill the scheduler

    # -- Internal: cron matching ------------------------------------

    @staticmethod
    def _field_matches(field: str, value: int) -> bool:
        """Match a single cron field against a value."""
        if field == "*":
            return True
        if field.startswith("*/"):
            step = int(field[2:])
            return step > 0 and value % step == 0
        if "," in field:
            return any(CronScheduler._field_matches(f.strip(), value)
                       for f in field.split(","))
        if "-" in field:
            lo, hi = field.split("-", 1)
            return int(lo) <= value <= int(hi)
        return value == int(field)

    @staticmethod
    def _cron_matches(cron_expr: str, dt: datetime) -> bool:
        """Check if a 5-field cron expression matches the given datetime.

        Standard cron semantics: DOM and DOW use OR when both are constrained.
        """
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return False
        minute, hour, dom, month, dow = fields
        dow_val = (dt.weekday() + 1) % 7  # Python Monday=0 -> cron Sunday=0

        m = CronScheduler._field_matches(minute, dt.minute)
        h = CronScheduler._field_matches(hour, dt.hour)
        dom_ok = CronScheduler._field_matches(dom, dt.day)
        month_ok = CronScheduler._field_matches(month, dt.month)
        dow_ok = CronScheduler._field_matches(dow, dow_val)

        if not (m and h and month_ok):
            return False

        dom_unconstrained = dom == "*"
        dow_unconstrained = dow == "*"
        if dom_unconstrained and dow_unconstrained:
            return True
        if dom_unconstrained:
            return dow_ok
        if dow_unconstrained:
            return dom_ok
        return dom_ok or dow_ok

    # -- Internal: validation ---------------------------------------

    @staticmethod
    def _validate_field(field: str, lo: int, hi: int) -> str | None:
        """Validate a single cron field value is within [lo, hi]."""
        if field == "*":
            return None
        if field.startswith("*/"):
            step_str = field[2:]
            if not step_str.isdigit():
                return f"Invalid step: {field}"
            step = int(step_str)
            if step <= 0:
                return f"Step must be > 0: {field}"
            return None
        if "," in field:
            for part in field.split(","):
                err = CronScheduler._validate_field(part.strip(), lo, hi)
                if err:
                    return err
            return None
        if "-" in field:
            parts = field.split("-", 1)
            if not parts[0].isdigit() or not parts[1].isdigit():
                return f"Invalid range: {field}"
            a, b = int(parts[0]), int(parts[1])
            if a < lo or a > hi or b < lo or b > hi:
                return f"Range {field} out of bounds [{lo}-{hi}]"
            if a > b:
                return f"Range start > end: {field}"
            return None
        if not field.isdigit():
            return f"Invalid field: {field}"
        val = int(field)
        if val < lo or val > hi:
            return f"Value {val} out of bounds [{lo}-{hi}]"
        return None

    @staticmethod
    def _validate_cron(cron_expr: str) -> str | None:
        """Validate a cron expression. Returns error message or None."""
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return f"Expected 5 fields, got {len(fields)}"
        bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        names = ["minute", "hour", "day-of-month", "month", "day-of-week"]
        for i, (field, (lo, hi), name) in enumerate(zip(fields, bounds, names)):
            err = CronScheduler._validate_field(field, lo, hi)
            if err:
                return f"{name}: {err}"
        return None

    # -- Internal: durable storage ----------------------------------

    def _save_durable(self):
        """Persist durable jobs to disk. Caller must hold _lock."""
        durable = [asdict(j) for j in self._jobs.values() if j.durable]
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(durable, indent=2))

    def _load_durable(self):
        """Load durable jobs from disk on startup."""
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text())
            for item in data:
                job = CronJob(**item)
                err = self._validate_cron(job.cron)
                if err:
                    continue
                self._jobs[job.id] = job
        except (json.JSONDecodeError, TypeError):
            pass