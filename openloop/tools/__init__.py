"""Tool registry."""

from .bash import BashTool
from .read import ReadFileTool
from .write import WriteFileTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .agent import AgentTool
from .search import SearchTool
from .visit import VisitTool
from .sandbox import (
    CreateSandboxTool,
    SandboxRunCommandTool,
    SandboxRunPythonTool,
    SandboxUploadFileTool,
    SandboxDownloadFileTool,
    SandboxReadFileTool,
    SandboxWriteFileTool,
    SandboxListFilesTool,
    SandboxKillTool,
)
from .skill import LoadSkillTool
from .todo import TodoWriteTool
from .plan_mode import EnterPlanModeTool, ExitPlanModeTool, WritePlanTool

ALL_TOOLS = [
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    AgentTool(),
    SearchTool(),
    VisitTool(),
    CreateSandboxTool(),
    SandboxRunCommandTool(),
    SandboxRunPythonTool(),
    SandboxUploadFileTool(),
    SandboxDownloadFileTool(),
    SandboxReadFileTool(),
    SandboxWriteFileTool(),
    SandboxListFilesTool(),
    SandboxKillTool(),
    TodoWriteTool(),
    EnterPlanModeTool(),
]

# Tools allowed in plan mode (read-only + plan-specific)
PLAN_MODE_TOOL_NAMES = {
    "read_file", "glob", "grep",
    "web_search", "web_visit",
    "load_skill", "search_knowledge",
    "list_crons", "todo_write",
    "write_plan", "enter_plan_mode", "exit_plan_mode",
    "sandbox_read", "sandbox_list_files",
}


def get_tool(name: str):
    """Look up a tool by name."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None
