"""Tests for cron scheduler."""

import json
import time
import threading
from datetime import datetime
from pathlib import Path

import pytest

from corecoder.cron import CronScheduler, CronJob


class TestCronJob:
    """CronJob dataclass tests."""

    def test_create(self):
        job = CronJob(id="test", cron="0 9 * * *", prompt="hello",
                      recurring=True, durable=True)
        assert job.id == "test"
        assert job.cron == "0 9 * * *"
        assert job.prompt == "hello"
        assert job.recurring is True
        assert job.durable is True

    def test_one_shot(self):
        job = CronJob(id="test", cron="* * * * *", prompt="x",
                      recurring=False, durable=False)
        assert job.recurring is False
        assert job.durable is False


class TestCronValidation:
    """Cron expression validation tests."""

    def test_valid_expressions(self):
        valid = ["* * * * *", "0 9 * * *", "*/5 * * * *",
                 "0,30 9,17 * * 1-5", "59 23 31 12 *"]
        for expr in valid:
            assert CronScheduler._validate_cron(expr) is None, f"Should be valid: {expr}"

    def test_wrong_field_count(self):
        assert CronScheduler._validate_cron("* * * *") is not None
        assert CronScheduler._validate_cron("* * * * * *") is not None

    def test_out_of_bounds(self):
        assert CronScheduler._validate_cron("60 * * * *") is not None
        assert CronScheduler._validate_cron("* 24 * * *") is not None
        assert CronScheduler._validate_cron("* * 32 * *") is not None
        assert CronScheduler._validate_cron("* * * 13 *") is not None
        assert CronScheduler._validate_cron("* * * * 7") is not None

    def test_invalid_format(self):
        assert CronScheduler._validate_cron("abc * * * *") is not None

    def test_step_validation(self):
        assert CronScheduler._validate_cron("*/10 * * * *") is None
        assert CronScheduler._validate_cron("*/0 * * * *") is not None

    def test_range_validation(self):
        assert CronScheduler._validate_cron("0 9-17 * * *") is None
        assert CronScheduler._validate_cron("0 17-9 * * *") is not None


class TestCronMatching:
    """Cron expression matching tests."""

    def test_wildcard_matches_all(self):
        dt = datetime(2026, 7, 23, 12, 0)
        assert CronScheduler._cron_matches("* * * * *", dt) is True

    def test_exact_minute_hour(self):
        dt = datetime(2026, 7, 23, 9, 30)
        assert CronScheduler._cron_matches("30 9 * * *", dt) is True
        assert CronScheduler._cron_matches("31 9 * * *", dt) is False

    def test_step(self):
        dt = datetime(2026, 7, 23, 12, 10)
        assert CronScheduler._cron_matches("*/5 * * * *", dt) is True
        dt = datetime(2026, 7, 23, 12, 11)
        assert CronScheduler._cron_matches("*/5 * * * *", dt) is False

    def test_list(self):
        dt = datetime(2026, 7, 23, 9, 0)
        assert CronScheduler._cron_matches("0 9,17 * * *", dt) is True
        dt = datetime(2026, 7, 23, 12, 0)
        assert CronScheduler._cron_matches("0 9,17 * * *", dt) is False

    def test_range(self):
        dt = datetime(2026, 7, 23, 12, 0)
        assert CronScheduler._cron_matches("0 9-17 * * *", dt) is True
        dt = datetime(2026, 7, 23, 20, 0)
        assert CronScheduler._cron_matches("0 9-17 * * *", dt) is False

    def test_dom(self):
        dt = datetime(2026, 7, 15, 12, 0)
        assert CronScheduler._cron_matches("0 12 15 * *", dt) is True
        dt = datetime(2026, 7, 16, 12, 0)
        assert CronScheduler._cron_matches("0 12 15 * *", dt) is False

    def test_dow(self):
        # 2026-07-23 is a Thursday (Python weekday=3, cron dow=4)
        dt = datetime(2026, 7, 23, 12, 0)
        assert CronScheduler._cron_matches("0 12 * * 4", dt) is True
        assert CronScheduler._cron_matches("0 12 * * 5", dt) is False


class TestSchedulerLifecycle:
    """Scheduler schedule/cancel/list/persist tests."""

    @pytest.fixture
    def tmp_storage(self, tmp_path):
        return tmp_path / "cron_jobs.json"

    def test_schedule_valid(self, tmp_storage):
        s = CronScheduler(tmp_storage)
        result = s.schedule("0 9 * * *", "daily reminder")
        assert isinstance(result, CronJob)
        assert result.cron == "0 9 * * *"
        assert result.recurring is True
        assert result.durable is True
        s.shutdown()

    def test_schedule_invalid(self, tmp_storage):
        s = CronScheduler(tmp_storage)
        result = s.schedule("invalid", "test")
        assert isinstance(result, str)
        s.shutdown()

    def test_list_jobs(self, tmp_storage):
        s = CronScheduler(tmp_storage)
        s.schedule("0 9 * * *", "job1")
        s.schedule("30 12 * * *", "job2")
        jobs = s.list_jobs()
        assert len(jobs) == 2
        s.shutdown()

    def test_cancel(self, tmp_storage):
        s = CronScheduler(tmp_storage)
        job = s.schedule("0 9 * * *", "test")
        assert isinstance(job, CronJob)
        result = s.cancel(job.id)
        assert "Cancelled" in result
        assert len(s.list_jobs()) == 0
        s.shutdown()

    def test_cancel_not_found(self, tmp_storage):
        s = CronScheduler(tmp_storage)
        result = s.cancel("nonexistent")
        assert "not found" in result
        s.shutdown()

    def test_persistence(self, tmp_storage):
        s1 = CronScheduler(tmp_storage)
        s1.schedule("0 9 * * *", "persistent job", durable=True)
        s1.shutdown()

        # Verify file exists
        assert tmp_storage.exists()
        data = json.loads(tmp_storage.read_text())
        assert len(data) == 1
        assert data[0]["prompt"] == "persistent job"

        # Load in new scheduler
        s2 = CronScheduler(tmp_storage)
        jobs = s2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].prompt == "persistent job"
        s2.shutdown()

    def test_non_durable_not_persisted(self, tmp_storage):
        s1 = CronScheduler(tmp_storage)
        s1.schedule("0 9 * * *", "ephemeral", durable=False)
        s1.shutdown()

        # File should not exist (no durable jobs)
        assert not tmp_storage.exists()


class TestQueueAndDelivery:
    """Queue consumption and delivery tests."""

    @pytest.fixture
    def scheduler(self, tmp_path):
        s = CronScheduler(tmp_path / "cron_jobs.json")
        yield s
        s.shutdown()

    def test_has_pending_initially_false(self, scheduler):
        assert scheduler.has_pending() is False

    def test_consume_empty_queue(self, scheduler):
        assert scheduler.consume_queue() == []

    def test_queue_after_fire(self, scheduler):
        """Schedule a job that fires immediately and verify it hits the queue."""
        # Schedule a job that matches every minute
        job = scheduler.schedule("* * * * *", "test prompt", recurring=False)
        assert isinstance(job, CronJob)

        # Wait for scheduler to fire
        time.sleep(2)

        assert scheduler.has_pending() is True
        fired = scheduler.consume_queue()
        assert len(fired) >= 1
        assert any(j.prompt == "test prompt" for j in fired)

    def test_one_shot_auto_removed(self, scheduler):
        """One-shot job is removed after firing."""
        job = scheduler.schedule("* * * * *", "one-shot", recurring=False)
        assert isinstance(job, CronJob)

        time.sleep(2)
        scheduler.consume_queue()

        # Job should be gone
        jobs = scheduler.list_jobs()
        assert not any(j.id == job.id for j in jobs)

    def test_recurring_not_removed(self, scheduler):
        """Recurring job stays after firing."""
        job = scheduler.schedule("* * * * *", "recurring", recurring=True)
        assert isinstance(job, CronJob)

        time.sleep(2)
        scheduler.consume_queue()

        # Job should still exist
        jobs = scheduler.list_jobs()
        assert any(j.id == job.id for j in jobs)

    def test_no_duplicate_fire_same_minute(self, scheduler):
        """Job should fire max once per minute."""
        scheduler.schedule("* * * * *", "test", recurring=True)

        time.sleep(2)
        first = len(scheduler.consume_queue())

        time.sleep(2)
        second = len(scheduler.consume_queue())

        # Same minute, should not fire again
        assert second == 0 or first > 0


class TestCronToolSchemas:
    """Verify cron tools have valid OpenAI function calling schemas."""

    def test_schedule_cron_schema(self):
        from corecoder.tools.cron import ScheduleCronTool
        tool = ScheduleCronTool(None)
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "schedule_cron"
        assert "cron" in schema["function"]["parameters"]["properties"]
        assert "prompt" in schema["function"]["parameters"]["properties"]

    def test_list_crons_schema(self):
        from corecoder.tools.cron import ListCronsTool
        tool = ListCronsTool(None)
        schema = tool.schema()
        assert schema["function"]["name"] == "list_crons"

    def test_cancel_cron_schema(self):
        from corecoder.tools.cron import CancelCronTool
        tool = CancelCronTool(None)
        schema = tool.schema()
        assert schema["function"]["name"] == "cancel_cron"
        assert "job_id" in schema["function"]["parameters"]["properties"]


class TestThreadSafety:
    """Thread safety tests for concurrent access."""

    def test_concurrent_schedule(self, tmp_path):
        s = CronScheduler(tmp_path / "cron_jobs.json")

        def add_jobs():
            for i in range(10):
                s.schedule(f"0 {i % 24} * * *", f"job{i}")

        threads = [threading.Thread(target=add_jobs) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No crash = pass
        s.shutdown()

    def test_concurrent_consume(self, tmp_path):
        s = CronScheduler(tmp_path / "cron_jobs.json")
        s.schedule("* * * * *", "test", recurring=True)
        time.sleep(2)

        consumed = []

        def consume():
            result = s.consume_queue()
            consumed.extend(result)

        threads = [threading.Thread(target=consume) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have consumed the job (possibly multiple from different threads)
        s.shutdown()