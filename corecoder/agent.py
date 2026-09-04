"""Core agent loop.

This is the heart of CoreCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import concurrent.futures
import inspect
import os
import re
import time
from pathlib import Path
from .llm import LLM
from .tools import ALL_TOOLS, PLAN_MODE_TOOL_NAMES
from .tools.base import Tool
from .tools.agent import AgentTool
from .tools.todo import TodoWriteTool
from .tools.skill import LoadSkillTool
from .tools.mcp import MCPTool, ConnectMCPTool, DisconnectMCPTool, ListMCPToolsTool
from .tools.cron import ScheduleCronTool, ListCronsTool, CancelCronTool
from .tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool, WritePlanTool
from .prompt import system_prompt
from .context import ContextManager
from .hooks import HookManager
from .skills import SkillManager
from .mcp import MCPClientManager
from .cron import CronScheduler
from .knowledge import KnowledgeManager
from .tools.cron import ScheduleCronTool, ListCronsTool, CancelCronTool
from .tools.knowledge import SearchKnowledgeTool, AddKnowledgeTool, IngestTool


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        hooks: HookManager | None = None,
        skills: SkillManager | None = None,
        mcp_manager: MCPClientManager | None = None,
        cron_scheduler: CronScheduler | None = None,
        knowledge_manager: KnowledgeManager | None = None,
    ):
        self.llm = llm
        self.skills = skills
        self.mcp_manager = mcp_manager
        self.cron_scheduler = cron_scheduler
        self.knowledge_manager = knowledge_manager

        # Todo tracking
        self.todos: list[dict] = []
        self._rounds_since_todo = 0

        # Plan mode state
        self.mode: str = "auto"  # "auto" or "plan"
        self._plan_file: str | None = None
        self._saved_tools: list[Tool] | None = None
        self._saved_system: str | None = None

        # Start with provided tools or default ALL_TOOLS
        if tools is not None:
            self.tools = list(tools)
        else:
            self.tools = list(ALL_TOOLS)

        # Add LoadSkillTool if skills are provided
        if skills is not None:
            self.tools.append(LoadSkillTool(skills))

        # Add MCP tools if mcp_manager is provided
        if mcp_manager is not None:
            self.tools.append(ConnectMCPTool(mcp_manager))
            self.tools.append(DisconnectMCPTool(mcp_manager))
            self.tools.append(ListMCPToolsTool(mcp_manager))

            for tool_info in mcp_manager.get_all_tools():
                self.tools.append(MCPTool(
                    server=tool_info["server"],
                    tool_name=tool_info["original_name"],
                    description=tool_info["description"],
                    parameters=tool_info["inputSchema"],
                    mcp_manager=mcp_manager
                ))

        # Add cron tools if cron_scheduler is provided
        if cron_scheduler is not None:
            self.tools.append(ScheduleCronTool(cron_scheduler))
            self.tools.append(ListCronsTool(cron_scheduler))
            self.tools.append(CancelCronTool(cron_scheduler))

        # Add knowledge tools if knowledge_manager is provided
        if knowledge_manager is not None:
            self.tools.append(SearchKnowledgeTool(knowledge_manager))
            self.tools.append(AddKnowledgeTool(knowledge_manager))
            self.tools.append(IngestTool(knowledge_manager))

        self._tool_by_name = {t.name: t for t in self.tools}
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self.hooks = hooks or HookManager()

        # Generate system prompt with skill catalog if available
        skill_catalog = skills.list_skills() if skills else ""
        self._system = system_prompt(self.tools, skill_catalog, self.mode)

        # wire up sub-agent capability
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self
            if isinstance(t, TodoWriteTool):
                t._agent = self
            if isinstance(t, EnterPlanModeTool):
                t._agent = self

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""

        # Consume any pending cron jobs before processing user input
        if self.cron_scheduler and self.cron_scheduler.has_pending():
            for job in self.cron_scheduler.consume_queue():
                self.messages.append({
                    "role": "user",
                    "content": f"[Scheduled: {job.id}] {job.prompt}",
                })

        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        # UserPromptSubmit hook — can block the prompt before LLM call
        blocked = self.hooks.trigger("UserPromptSubmit", {
            "user_input": user_input,
            "messages": self.messages,
        })
        if blocked:
            self.messages.pop()  # keep history clean
            return f"(blocked: {blocked})"

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                # Stop hook — observe-only, fires before returning
                self.hooks.trigger("Stop", {
                    "response": resp.content,
                    "messages": self.messages,
                })
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            try:
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)

                    # PreToolUse hook — can block the tool
                    blocked = self.hooks.trigger("PreToolUse", {
                        "tool_name": tc.name,
                        "tool_input": tc.arguments,
                        "tool_call_id": tc.id,
                    })
                    if blocked:
                        result = f"(blocked by hook: {blocked})"
                    else:
                        result = self._exec_tool(tc)

                    # PostToolUse hook — observe-only
                    self.hooks.trigger("PostToolUse", {
                        "tool_name": tc.name,
                        "tool_input": tc.arguments,
                        "tool_call_id": tc.id,
                        "result": result,
                    })

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    # parallel execution for multiple tool calls
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc, result in zip(resp.tool_calls, results):
                        # PostToolUse hook — observe-only
                        self.hooks.trigger("PostToolUse", {
                            "tool_name": tc.name,
                            "tool_input": tc.arguments,
                            "tool_call_id": tc.id,
                            "result": result,
                        })
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
            except KeyboardInterrupt:
                # Ctrl+C mid-execution would leave the assistant tool_calls
                # message without replies, poisoning the next request; backfill
                self._answer_pending_tool_calls(resp.tool_calls)
                raise

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

            # Nag reminder: inject if agent hasn't updated todos for 3 rounds
            self._rounds_since_todo += 1
            if self._rounds_since_todo >= 3 and self.todos:
                self.messages.append({
                    "role": "user",
                    "content": "<reminder>You haven't updated your todo list in 3 rounds. "
                               "Use todo_write to update your progress.</reminder>",
                })
                self._rounds_since_todo = 0

        # Stop hook at max rounds exit
        self.hooks.trigger("Stop", {
            "response": "(reached maximum tool-call rounds)",
            "messages": self.messages,
        })
        return "(reached maximum tool-call rounds)"

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
        tool = self._tool_by_name.get(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        # validate arguments first so a TypeError raised *inside* the tool isn't
        # mislabelled as a bad-arguments error from the caller
        try:
            inspect.signature(tool.execute).bind(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        try:
            return tool.execute(**tc.arguments)
        except Exception as e:
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """Run multiple tool calls concurrently using threads.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, run them in parallel.

        PreToolUse hooks run in the main thread before dispatch to avoid
        race conditions on blocking decisions.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        # PreToolUse hooks — checked in main thread, serialized
        blocked_mask: list[str | None] = []
        for tc in tool_calls:
            reason = self.hooks.trigger("PreToolUse", {
                "tool_name": tc.name,
                "tool_input": tc.arguments,
                "tool_call_id": tc.id,
            })
            blocked_mask.append(reason)

        def _maybe_exec(tc_and_blocked):
            tc, blocked_reason = tc_and_blocked
            if blocked_reason is not None:
                return f"(blocked by hook: {blocked_reason})"
            return self._exec_tool(tc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(_maybe_exec, pair)
                for pair in zip(tool_calls, blocked_mask)
            ]
            return [f.result() for f in futures]

    def _answer_pending_tool_calls(self, tool_calls):
        """Backfill a tool reply for every call that didn't get one.

        OpenAI-compatible APIs reject a request where an assistant message has
        tool_calls without a matching tool reply for each id, so this keeps the
        history valid when execution is interrupted partway through.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id not in answered:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[interrupted]",
                })

    def reset(self):
        """Clear conversation history and clean up active sandboxes."""
        self.messages.clear()
        try:
            from .tools.sandbox import kill_all_sandboxes
            kill_all_sandboxes()
        except Exception:
            pass

    def __del__(self):
        """Ensure active sandbox containers are cleaned up upon destruction."""
        try:
            from .tools.sandbox import kill_all_sandboxes
            kill_all_sandboxes()
        except Exception:
            pass

    # ── Plan Mode ──────────────────────────────────────────────

    def _slugify(self, text: str) -> str:
        """Convert a task description into a safe filename slug."""
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug.strip("-")[:60]

    def _rebuild_tools(self):
        """Rebuild _tool_by_name from current self.tools list."""
        self._tool_by_name = {t.name: t for t in self.tools}

    def _rebuild_system_prompt(self):
        """Rebuild system prompt with current mode context."""
        skill_catalog = self.skills.list_skills() if self.skills else ""
        plan_file = self._plan_file if self.mode == "plan" else None
        self._system = system_prompt(self.tools, skill_catalog, self.mode, plan_file)

    def enter_plan_mode(self, task: str = ""):
        """Switch to plan mode: restrict tools to read-only + plan tools."""
        if self.mode == "plan":
            return

        # Save current state for restoration
        self._saved_tools = list(self.tools)
        self._saved_system = self._system

        # Generate plan file path
        slug = self._slugify(task) if task else f"plan-{int(time.time())}"
        plan_dir = Path(os.getcwd()) / ".corecoder" / "plans"
        self._plan_file = str(plan_dir / f"{slug}.md")

        self.mode = "plan"

        # Filter tools to plan-mode whitelist
        plan_tools = [t for t in self._saved_tools
                      if t.name in PLAN_MODE_TOOL_NAMES]
        # Add ExitPlanMode and WritePlan tools (not in ALL_TOOLS)
        exit_tool = ExitPlanModeTool()
        exit_tool._agent = self
        plan_tools.append(exit_tool)

        write_tool = WritePlanTool()
        write_tool._agent = self
        plan_tools.append(write_tool)

        self.tools = plan_tools
        self._rebuild_tools()
        self._rebuild_system_prompt()

    def exit_plan_mode(self):
        """Exit plan mode: restore full tools and system prompt."""
        if self.mode != "plan":
            return

        self.mode = "auto"
        self._plan_file = None

        if self._saved_tools is not None:
            self.tools = self._saved_tools
            self._saved_tools = None
        if self._saved_system is not None:
            self._system = self._saved_system
            self._saved_system = None

        self._rebuild_tools()
