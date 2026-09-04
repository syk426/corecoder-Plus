"""todo_write tool - task planning and tracking for the agent."""

from .base import Tool


class TodoWriteTool(Tool):
    """Tool for creating and managing a task list during a coding session."""

    name = "todo_write"
    description = (
        "Create and manage a task list for your current coding session. "
        "Use this before starting any multi-step task to plan your steps. "
        "Update status as you go: pending, in_progress, completed. "
        "Only one task can be in_progress at a time."
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "List of tasks with content and status",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Task description",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Task status",
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    def __init__(self):
        self._agent = None

    def execute(self, todos: list[dict]) -> str:
        if self._agent is None:
            return "Error: todo_write not initialized"

        # Validate
        for i, t in enumerate(todos):
            if not isinstance(t, dict):
                return f"Error: todos[{i}] must be an object"
            if "content" not in t or "status" not in t:
                return f"Error: todos[{i}] missing 'content' or 'status'"
            if t["status"] not in ("pending", "in_progress", "completed"):
                return f"Error: todos[{i}] invalid status '{t['status']}'"

        self._agent.todos = todos
        self._agent._rounds_since_todo = 0

        # Format output
        icon = {"pending": "○", "in_progress": "●", "completed": "✓"}
        lines = []
        for t in todos:
            lines.append(f"  [{icon[t['status']]}] {t['content']}")
        return "\n".join(lines)