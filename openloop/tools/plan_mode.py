"""Plan mode tools — enter_plan_mode, exit_plan_mode, write_plan.

These tools let the agent switch into a read-only planning mode,
write a plan file, and exit back to normal mode for user approval.
"""

import os
import re
import time
from pathlib import Path

from .base import Tool


def _slugify(text: str) -> str:
    """Convert a task description into a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")[:60]


class EnterPlanModeTool(Tool):
    """Enter plan mode — restricts tools to read-only + write_plan."""

    name = "enter_plan_mode"
    description = (
        "Enter plan mode to design an implementation approach before writing code. "
        "In plan mode you can only use read-only tools (read_file, glob, grep, etc.) "
        "plus write_plan to write your plan. Call exit_plan_mode when your plan is ready."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Description of the task to plan",
            },
        },
        "required": ["task"],
    }

    def __init__(self):
        self._agent = None

    def execute(self, task: str) -> str:
        if self._agent is None:
            return "Error: enter_plan_mode not initialized"

        self._agent.enter_plan_mode(task)
        plan_file = self._agent._plan_file
        return (
            f"Entered plan mode. You can now only use read-only tools.\n"
            f"Explore the codebase, design your approach, then use write_plan "
            f"to write your plan to: {plan_file}\n"
            f"When ready, call exit_plan_mode to present the plan for approval."
        )


class ExitPlanModeTool(Tool):
    """Exit plan mode — restore full tools and present the plan for approval."""

    name = "exit_plan_mode"
    description = (
        "Exit plan mode and present your plan for user approval. "
        "Only call this after you have written a complete plan with write_plan."
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan_summary": {
                "type": "string",
                "description": "Brief summary of the plan (2-3 sentences)",
            },
        },
        "required": ["plan_summary"],
    }

    def __init__(self):
        self._agent = None

    def execute(self, plan_summary: str) -> str:
        if self._agent is None:
            return "Error: exit_plan_mode not initialized"

        plan_file = self._agent._plan_file
        plan_content = ""
        if plan_file and os.path.exists(plan_file):
            plan_content = Path(plan_file).read_text(encoding="utf-8")

        self._agent.exit_plan_mode()

        return (
            f"Exited plan mode. Full tools restored.\n\n"
            f"## Plan Summary\n{plan_summary}\n\n"
            f"## Plan File\n{plan_file}\n\n"
            f"```markdown\n{plan_content}\n```"
        )


class WritePlanTool(Tool):
    """Write the plan file. Only allowed inside plan mode."""

    name = "write_plan"
    description = (
        "Write your implementation plan to the plan file. "
        "You can call this multiple times to update the plan as you refine it. "
        "Only works in plan mode."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Full plan content in markdown format",
            },
        },
        "required": ["content"],
    }

    def __init__(self):
        self._agent = None

    def execute(self, content: str) -> str:
        if self._agent is None:
            return "Error: write_plan not initialized"

        if self._agent.mode != "plan":
            return "Error: write_plan can only be used in plan mode"

        plan_file = self._agent._plan_file
        if not plan_file:
            return "Error: no plan file configured — call enter_plan_mode first"

        plan_path = Path(plan_file)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(content, encoding="utf-8")

        return f"Plan written to {plan_file} ({len(content)} chars)"