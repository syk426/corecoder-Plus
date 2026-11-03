"""Cron tools - schedule, list, and cancel cron jobs."""

from .base import Tool


class ScheduleCronTool(Tool):
    """Tool to schedule a cron job."""

    name = "schedule_cron"
    description = (
        "Schedule a recurring or one-shot task. "
        "cron is a 5-field expression: minute hour day-of-month month day-of-week. "
        "Examples: '0 9 * * *' (9am daily), '*/5 * * * *' (every 5 min), "
        "'30 14 15 * *' (2:30pm on the 15th)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "cron": {
                "type": "string",
                "description": "5-field cron expression: minute hour dom month dow"
            },
            "prompt": {
                "type": "string",
                "description": "Message to inject when the job fires"
            },
            "recurring": {
                "type": "boolean",
                "description": "True for recurring, False for one-shot. Default: True"
            },
            "durable": {
                "type": "boolean",
                "description": "True to persist across restarts. Default: True"
            },
        },
        "required": ["cron", "prompt"],
    }

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def execute(self, cron: str, prompt: str, recurring: bool = True,
                durable: bool = True) -> str:
        result = self.scheduler.schedule(cron, prompt, recurring, durable)
        if isinstance(result, str):
            return f"Error: {result}"
        return f"Scheduled {result.id}: '{cron}' -> {prompt}"


class ListCronsTool(Tool):
    """Tool to list all registered cron jobs."""

    name = "list_crons"
    description = "List all registered cron jobs with their IDs, schedules, and status."
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def execute(self) -> str:
        jobs = self.scheduler.list_jobs()
        if not jobs:
            return "No cron jobs registered."
        lines = []
        for j in jobs:
            tag = "recurring" if j.recurring else "one-shot"
            dur = "durable" if j.durable else "session"
            lines.append(f"  {j.id}: '{j.cron}' -> {j.prompt} [{tag}, {dur}]")
        return "\n".join(lines)


class CancelCronTool(Tool):
    """Tool to cancel a cron job."""

    name = "cancel_cron"
    description = "Cancel a cron job by ID."
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "The ID of the cron job to cancel"
            },
        },
        "required": ["job_id"],
    }

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def execute(self, job_id: str) -> str:
        return self.scheduler.cancel(job_id)