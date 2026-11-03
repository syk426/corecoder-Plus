"""Hook system — lifecycle event callbacks for the agent.

Supports two forms:
  1. Python callbacks: register_hook(event, fn)
  2. Shell commands: register_shell_hook(event, matcher, command)

Events:
  UserPromptSubmit — user input received, before LLM call (can block)
  PreToolUse       — before tool execution (can block)
  PostToolUse      — after tool execution (observe only)
  Stop             — about to return final response (observe only)
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

# A hook callback: receives context dict, returns None (allow) or str (block reason)
HookCallback = Callable[[dict[str, Any]], str | None]

VALID_EVENTS = frozenset({"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})


# ═══════════════════════════════════════════════════════════
#  Data Types
# ═══════════════════════════════════════════════════════════

@dataclass
class ShellHook:
    """A shell command hook loaded from settings.json."""
    event: str
    matcher: str          # tool name pattern: "*", "bash", "write_file|edit_file"
    command: str          # shell command to execute
    timeout: int = 30     # safety timeout in seconds


@dataclass
class HookEntry:
    """A registered hook (either Python callback or shell command)."""
    event: str
    callback: HookCallback | None = None
    shell: ShellHook | None = None


# ═══════════════════════════════════════════════════════════
#  HookManager
# ═══════════════════════════════════════════════════════════

class HookManager:
    """Thread-safe hook registry and executor.

    Thread safety: registration takes a lock; triggering copies the list
    under the lock then iterates the snapshot without it. This means
    callbacks can safely call register_hook() without deadlock.
    """

    def __init__(self, verbose: bool = False):
        self._hooks: dict[str, list[HookEntry]] = {e: [] for e in VALID_EVENTS}
        self._lock = threading.Lock()
        self.verbose = verbose

    # ── Registration ──────────────────────────────────────

    def register_hook(self, event: str, callback: HookCallback) -> None:
        """Register a Python callback for an event."""
        if event not in VALID_EVENTS:
            raise ValueError(f"Unknown event: {event}. Valid: {sorted(VALID_EVENTS)}")
        with self._lock:
            self._hooks[event].append(HookEntry(event=event, callback=callback))

    def register_shell_hook(
        self, event: str, matcher: str, command: str, timeout: int = 30
    ) -> None:
        """Register a shell command hook."""
        if event not in VALID_EVENTS:
            raise ValueError(f"Unknown event: {event}. Valid: {sorted(VALID_EVENTS)}")
        shell = ShellHook(event=event, matcher=matcher, command=command, timeout=timeout)
        with self._lock:
            self._hooks[event].append(HookEntry(event=event, shell=shell))

    # ── Triggering ────────────────────────────────────────

    def trigger(self, event: str, context: dict[str, Any]) -> str | None:
        """Fire all hooks for an event. Returns block reason (str) or None.

        Short-circuits on first block: if any hook returns non-None,
        subsequent hooks are skipped and the reason is returned immediately.
        """
        if event not in VALID_EVENTS:
            return None

        # Snapshot under lock, iterate without it
        with self._lock:
            entries = list(self._hooks[event])

        if self.verbose:
            tool_name = context.get("tool_name", "")
            suffix = f"({tool_name})" if tool_name else ""
            print(f"\n[HOOK] {event}{suffix} → ", end="", flush=True)

        start_total = time.time()

        for entry in entries:
            if entry.callback is not None:
                result = entry.callback(context)
                if result is not None:
                    if self.verbose:
                        duration = (time.time() - start_total) * 1000
                        print(f"BLOCKED: {result} ({duration:.1f}ms)")
                    return result
            elif entry.shell is not None:
                result = _run_shell_hook(entry.shell, context)
                if result is not None:
                    if self.verbose:
                        duration = (time.time() - start_total) * 1000
                        print(f"BLOCKED: {result} ({duration:.1f}ms)")
                    return result

        if self.verbose:
            duration = (time.time() - start_total) * 1000
            if not entries:
                print(f"no hooks registered ({duration:.1f}ms)")
            else:
                print(f"allowed ({duration:.1f}ms)")

        return None

    # ── Cleanup ───────────────────────────────────────────

    def clear(self, event: str | None = None) -> None:
        """Remove all hooks, or all hooks for a specific event."""
        with self._lock:
            if event is None:
                for e in VALID_EVENTS:
                    self._hooks[e].clear()
            elif event in VALID_EVENTS:
                self._hooks[event].clear()

    # ── Settings Loading ──────────────────────────────────

    def load_from_settings(self, settings: dict) -> None:
        """Load shell hooks from a settings.json dict.

        Expected schema::

            {
                "hooks_verbose": true,
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "bash", "command": "echo check", "timeout": 5}
                    ]
                }
            }
        """
        # Load verbose setting
        self.verbose = settings.get("hooks_verbose", False)

        hooks_config = settings.get("hooks", {})
        for event_name, hook_list in hooks_config.items():
            if event_name not in VALID_EVENTS:
                continue  # silently skip unknown events
            for hook_def in hook_list:
                matcher = hook_def.get("matcher", "*")
                command = hook_def.get("command", "")
                timeout = hook_def.get("timeout", 30)
                if command:
                    self.register_shell_hook(event_name, matcher, command, timeout)


# ═══════════════════════════════════════════════════════════
#  Shell Hook Execution
# ═══════════════════════════════════════════════════════════

def _matches_tool(matcher: str, tool_name: str) -> bool:
    """Check if a tool name matches a matcher pattern.

    Supports:
    - ``"*"`` — matches everything
    - exact name: ``"bash"`` — matches only ``"bash"``
    - regex: ``"write_file|edit_file"`` — matches either
    - comma-separated: ``"bash,write_file"`` — matches either
    """
    if matcher == "*":
        return True
    if "," in matcher:
        return tool_name in {m.strip() for m in matcher.split(",")}
    try:
        return bool(re.fullmatch(matcher, tool_name))
    except re.error:
        return matcher == tool_name


def _run_shell_hook(shell: ShellHook, context: dict[str, Any]) -> str | None:
    """Execute a shell hook command. Returns block reason or None.

    Protocol:
    - Context JSON passed via stdin (with "event" field added)
    - Exit 0 = allow (return None)
    - Exit non-zero = block (stdout becomes reason)
    - Timeout = block with timeout message
    """
    # Check matcher for tool events
    tool_name = context.get("tool_name", "")
    if tool_name and not _matches_tool(shell.matcher, tool_name):
        return None  # skip — tool doesn't match

    # Add event name to context for the shell script
    full_context = {"event": shell.event, **context}

    try:
        proc = subprocess.run(
            shell.command,
            shell=True,
            input=json.dumps(full_context),
            capture_output=True,
            text=True,
            timeout=shell.timeout,
        )
        if proc.returncode != 0:
            reason = (
                proc.stdout.strip()
                or proc.stderr.strip()
                or f"Hook blocked (exit {proc.returncode})"
            )
            return reason
        return None
    except subprocess.TimeoutExpired:
        return f"Hook timed out after {shell.timeout}s"
    except Exception as e:
        return f"Hook error: {e}"


# ═══════════════════════════════════════════════════════════
#  Built-in Example Hooks
# ═══════════════════════════════════════════════════════════

def log_hook(context: dict) -> None:
    """Built-in: logs every tool call. Demonstrates observe-only pattern."""
    tool = context.get("tool_name", "unknown")
    print(f"[HOOK LOG] {tool}({context.get('tool_input', {})})")
    return None


def permission_hook(context: dict) -> str | None:
    """Built-in: blocks dangerous bash commands. Demonstrates blocking pattern."""
    if context.get("tool_name") != "bash":
        return None
    cmd = context.get("tool_input", {}).get("command", "")
    deny_list = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]
    for pattern in deny_list:
        if pattern in cmd:
            return f"Permission denied: '{pattern}' is blocked by hook"
    return None


# ═══════════════════════════════════════════════════════════
#  Module-level Convenience API
# ═══════════════════════════════════════════════════════════

_default_manager = HookManager()


def register_hook(event: str, callback: HookCallback) -> None:
    """Register on the default HookManager (module-level convenience)."""
    _default_manager.register_hook(event, callback)


def trigger_hooks(event: str, context: dict) -> str | None:
    """Trigger on the default HookManager (module-level convenience)."""
    return _default_manager.trigger(event, context)


def get_default_manager() -> HookManager:
    """Return the default HookManager singleton."""
    return _default_manager


def reset_default_manager() -> None:
    """Clear the default manager (useful for testing)."""
    _default_manager.clear()
