"""Tests for the hook system."""

import json
import os
import tempfile
import threading

import pytest

from openloop.hooks import (
    HookManager,
    ShellHook,
    VALID_EVENTS,
    _matches_tool,
    _run_shell_hook,
    log_hook,
    permission_hook,
    register_hook,
    reset_default_manager,
    trigger_hooks,
)


# ═══════════════════════════════════════════════════════════
#  HookManager Basics
# ═══════════════════════════════════════════════════════════

class TestHookManagerBasics:

    def test_valid_events(self):
        assert "UserPromptSubmit" in VALID_EVENTS
        assert "PreToolUse" in VALID_EVENTS
        assert "PostToolUse" in VALID_EVENTS
        assert "Stop" in VALID_EVENTS

    def test_register_and_trigger(self):
        mgr = HookManager()
        called = []

        def my_hook(ctx):
            called.append(ctx)
            return None

        mgr.register_hook("PreToolUse", my_hook)
        result = mgr.trigger("PreToolUse", {"tool_name": "bash"})
        assert result is None
        assert len(called) == 1
        assert called[0]["tool_name"] == "bash"

    def test_blocking_hook(self):
        mgr = HookManager()
        mgr.register_hook("PreToolUse", lambda ctx: "blocked!")
        result = mgr.trigger("PreToolUse", {"tool_name": "bash"})
        assert result == "blocked!"

    def test_non_blocking_returns_none(self):
        mgr = HookManager()
        mgr.register_hook("PreToolUse", lambda ctx: None)
        result = mgr.trigger("PreToolUse", {"tool_name": "bash"})
        assert result is None

    def test_short_circuit_on_block(self):
        """First blocking hook wins; later hooks don't run."""
        mgr = HookManager()
        order = []

        def first(ctx):
            order.append("first")
            return "nope"

        def second(ctx):
            order.append("second")
            return None

        mgr.register_hook("PreToolUse", first)
        mgr.register_hook("PreToolUse", second)
        result = mgr.trigger("PreToolUse", {})
        assert result == "nope"
        assert order == ["first"]

    def test_invalid_event_raises(self):
        mgr = HookManager()
        with pytest.raises(ValueError, match="Unknown event"):
            mgr.register_hook("FakeEvent", lambda ctx: None)

    def test_trigger_unknown_event_returns_none(self):
        mgr = HookManager()
        assert mgr.trigger("FakeEvent", {}) is None

    def test_clear_all(self):
        mgr = HookManager()
        mgr.register_hook("PreToolUse", lambda ctx: "block")
        mgr.register_hook("Stop", lambda ctx: "block")
        mgr.clear()
        for event in VALID_EVENTS:
            assert mgr.trigger(event, {}) is None

    def test_clear_specific_event(self):
        mgr = HookManager()
        mgr.register_hook("PreToolUse", lambda ctx: "block")
        mgr.register_hook("Stop", lambda ctx: "block")
        mgr.clear("PreToolUse")
        assert mgr.trigger("PreToolUse", {}) is None
        assert mgr.trigger("Stop", {}) == "block"

    def test_multiple_hooks_same_event(self):
        mgr = HookManager()
        results = []
        mgr.register_hook("PreToolUse", lambda ctx: (results.append(1), None)[1])
        mgr.register_hook("PreToolUse", lambda ctx: (results.append(2), None)[1])
        mgr.register_hook("PreToolUse", lambda ctx: (results.append(3), None)[1])
        mgr.trigger("PreToolUse", {})
        assert results == [1, 2, 3]

    def test_hooks_isolated_across_events(self):
        mgr = HookManager()
        mgr.register_hook("PreToolUse", lambda ctx: "pre")
        mgr.register_hook("PostToolUse", lambda ctx: "post")
        assert mgr.trigger("PreToolUse", {}) == "pre"
        assert mgr.trigger("PostToolUse", {}) == "post"
        assert mgr.trigger("Stop", {}) is None


# ═══════════════════════════════════════════════════════════
#  Matcher Logic
# ═══════════════════════════════════════════════════════════

class TestMatcher:

    def test_star_matches_all(self):
        assert _matches_tool("*", "bash") is True
        assert _matches_tool("*", "write_file") is True
        assert _matches_tool("*", "anything") is True

    def test_exact_match(self):
        assert _matches_tool("bash", "bash") is True
        assert _matches_tool("bash", "write_file") is False

    def test_regex_alternation(self):
        assert _matches_tool("write_file|edit_file", "write_file") is True
        assert _matches_tool("write_file|edit_file", "edit_file") is True
        assert _matches_tool("write_file|edit_file", "bash") is False

    def test_comma_separated(self):
        assert _matches_tool("bash,write_file", "bash") is True
        assert _matches_tool("bash,write_file", "write_file") is True
        assert _matches_tool("bash,write_file", "edit_file") is False

    def test_regex_pattern(self):
        assert _matches_tool("sandbox_.*", "sandbox_create") is True
        assert _matches_tool("sandbox_.*", "sandbox_run_command") is True
        assert _matches_tool("sandbox_.*", "bash") is False

    def test_invalid_regex_falls_back_to_exact(self):
        # "[invalid" is a bad regex
        assert _matches_tool("[invalid", "[invalid") is True
        assert _matches_tool("[invalid", "bash") is False


# ═══════════════════════════════════════════════════════════
#  Shell Hooks
# ═══════════════════════════════════════════════════════════

class TestShellHooks:

    def test_shell_hook_allow(self):
        shell = ShellHook(event="PreToolUse", matcher="*", command="exit 0")
        result = _run_shell_hook(shell, {"tool_name": "bash"})
        assert result is None

    def test_shell_hook_block(self):
        shell = ShellHook(
            event="PreToolUse", matcher="*",
            command="echo 'nope' && exit 1"
        )
        result = _run_shell_hook(shell, {"tool_name": "bash"})
        assert result == "nope"

    def test_shell_hook_receives_json_stdin(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmp = f.name

        try:
            shell = ShellHook(
                event="PreToolUse", matcher="*",
                command=f"cat > {tmp}"
            )
            _run_shell_hook(shell, {"tool_name": "bash", "foo": "bar"})
            data = json.loads(open(tmp).read())
            assert data["tool_name"] == "bash"
            assert data["foo"] == "bar"
            assert data["event"] == "PreToolUse"
        finally:
            os.unlink(tmp)

    def test_shell_hook_matcher_skips_non_matching(self):
        shell = ShellHook(
            event="PreToolUse", matcher="write_file",
            command="exit 1"
        )
        result = _run_shell_hook(shell, {"tool_name": "bash"})
        assert result is None  # skipped, not blocked

    def test_shell_hook_timeout(self):
        shell = ShellHook(
            event="PreToolUse", matcher="*",
            command="sleep 10", timeout=1
        )
        result = _run_shell_hook(shell, {"tool_name": "bash"})
        assert "timed out" in result.lower()

    def test_shell_hook_stderr_as_reason(self):
        shell = ShellHook(
            event="PreToolUse", matcher="*",
            command="echo 'err msg' >&2 && exit 1"
        )
        result = _run_shell_hook(shell, {"tool_name": "bash"})
        assert result == "err msg"

    def test_shell_hook_no_output_default_reason(self):
        shell = ShellHook(
            event="PreToolUse", matcher="*",
            command="exit 1"
        )
        result = _run_shell_hook(shell, {"tool_name": "bash"})
        assert "blocked" in result.lower() or "exit" in result.lower()

    def test_register_shell_hook_via_manager(self):
        mgr = HookManager()
        mgr.register_shell_hook("PreToolUse", "bash", "echo 'no' && exit 1")
        result = mgr.trigger("PreToolUse", {"tool_name": "bash"})
        assert result == "no"

    def test_load_from_settings(self):
        mgr = HookManager()
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "bash", "command": "exit 0"},
                    {"matcher": "*", "command": "echo check"},
                ],
                "Stop": [
                    {"command": "exit 0"}
                ],
            }
        }
        mgr.load_from_settings(settings)
        assert len(mgr._hooks["PreToolUse"]) == 2
        assert len(mgr._hooks["Stop"]) == 1
        assert len(mgr._hooks["UserPromptSubmit"]) == 0

    def test_load_from_settings_skips_unknown_events(self):
        mgr = HookManager()
        settings = {"hooks": {"FakeEvent": [{"command": "exit 0"}]}}
        mgr.load_from_settings(settings)  # should not raise

    def test_load_from_settings_skips_empty_command(self):
        mgr = HookManager()
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "command": ""}]}}
        mgr.load_from_settings(settings)
        assert len(mgr._hooks["PreToolUse"]) == 0


# ═══════════════════════════════════════════════════════════
#  Thread Safety
# ═══════════════════════════════════════════════════════════

class TestThreadSafety:

    def test_concurrent_trigger(self):
        mgr = HookManager()
        counter = {"n": 0}
        lock = threading.Lock()

        def counting_hook(ctx):
            with lock:
                counter["n"] += 1
            return None

        mgr.register_hook("PreToolUse", counting_hook)

        def fire():
            mgr.trigger("PreToolUse", {"tool_name": "bash"})

        threads = [threading.Thread(target=fire) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter["n"] == 20

    def test_register_during_trigger(self):
        """Registering a hook while another trigger is in progress should not deadlock."""
        mgr = HookManager()

        def registering_hook(ctx):
            mgr.register_hook("PreToolUse", lambda c: None)
            return None

        mgr.register_hook("PreToolUse", registering_hook)
        result = mgr.trigger("PreToolUse", {})
        assert result is None


# ═══════════════════════════════════════════════════════════
#  Module-level API
# ═══════════════════════════════════════════════════════════

class TestModuleLevelAPI:

    def test_register_and_trigger(self):
        reset_default_manager()
        register_hook("PreToolUse", lambda ctx: "module-block")
        result = trigger_hooks("PreToolUse", {"tool_name": "bash"})
        assert result == "module-block"
        reset_default_manager()
        assert trigger_hooks("PreToolUse", {}) is None

    def test_reset_clears_all(self):
        reset_default_manager()
        register_hook("PreToolUse", lambda ctx: "block")
        register_hook("Stop", lambda ctx: "block")
        reset_default_manager()
        assert trigger_hooks("PreToolUse", {}) is None
        assert trigger_hooks("Stop", {}) is None


# ═══════════════════════════════════════════════════════════
#  Built-in Hooks
# ═══════════════════════════════════════════════════════════

class TestBuiltinHooks:

    def test_log_hook_returns_none(self):
        result = log_hook({"tool_name": "bash", "tool_input": {"command": "ls"}})
        assert result is None

    def test_permission_hook_blocks_dangerous(self):
        result = permission_hook({
            "tool_name": "bash",
            "tool_input": {"command": "rm -rf /"},
        })
        assert result is not None
        assert "blocked" in result.lower() or "denied" in result.lower()

    def test_permission_hook_allows_safe(self):
        result = permission_hook({
            "tool_name": "bash",
            "tool_input": {"command": "ls -la"},
        })
        assert result is None

    def test_permission_hook_skips_non_bash(self):
        result = permission_hook({
            "tool_name": "write_file",
            "tool_input": {"path": "/tmp/test"},
        })
        assert result is None
