"""Unit tests for Alibaba OpenSandbox integration and the 9 atomic sandbox tools."""

import time
from unittest.mock import MagicMock

from corecoder.tools import get_tool
from corecoder.tools.sandbox import (
    ACTIVE_SANDBOXES,
    CreateSandboxTool,
    SandboxDownloadFileTool,
    SandboxKillTool,
    SandboxListFilesTool,
    SandboxReadFileTool,
    SandboxRunCommandTool,
    SandboxRunPythonTool,
    SandboxUploadFileTool,
    SandboxWriteFileTool,
    get_active_sandboxes,
    kill_all_sandboxes,
    kill_sandbox,
)


def test_all_nine_sandbox_tools_registered_and_valid():
    """Verify that all 9 atomic OpenSandbox tools exist and generate valid OpenAI schemas."""
    tool_names = [
        "sandbox_create",
        "sandbox_run_command",
        "sandbox_run_python",
        "sandbox_upload",
        "sandbox_download",
        "sandbox_read",
        "sandbox_write",
        "sandbox_list_files",
        "sandbox_kill",
    ]
    for name in tool_names:
        tool = get_tool(name)
        assert tool is not None, f"Tool {name} is not registered in ALL_TOOLS!"
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == name
        assert "description" in schema["function"]


def test_invalid_sandbox_id_rejections():
    """Ensure invalid or placeholder sandbox IDs are caught before attempting network calls."""
    cmd_tool = SandboxRunCommandTool()
    res = cmd_tool.execute("ls", sandbox_id="default")
    assert "[ERROR]" in res
    assert "not a valid sandbox_id" in res

    upload_tool = SandboxUploadFileTool()
    res = upload_tool.execute("temp", "/nonexistent/file")
    assert "[ERROR]" in res

    read_tool = SandboxReadFileTool()
    res = read_tool.execute("sandbox", "/work/data.txt")
    assert "[ERROR]" in res
    assert "not a valid sandbox_id" in res


def test_sandbox_lifecycle_manager_and_table():
    """Test the in-memory active registry and graceful termination logic."""
    ACTIVE_SANDBOXES.clear()

    # Create a mock instance object
    mock_instance = MagicMock()
    ACTIVE_SANDBOXES["sbx-101"] = {
        "id": "sbx-101",
        "image": "python:3.12",
        "created_at": time.time() - 30,  # 30 seconds uptime
        "timeout": 600,
        "instance": mock_instance,
        "status": "Running",
    }

    # Verify get_active_sandboxes format for CLI dashboard
    sandboxes = get_active_sandboxes()
    assert len(sandboxes) == 1
    assert sandboxes[0]["id"] == "sbx-101"
    assert sandboxes[0]["image"] == "python:3.12"
    assert sandboxes[0]["uptime_seconds"] >= 30
    assert sandboxes[0]["remaining_seconds"] <= 570
    assert sandboxes[0]["engine"] == "Alibaba OpenSandbox"

    # Verify individual kill
    success = kill_sandbox("sbx-101")
    assert success is True
    assert "sbx-101" not in ACTIVE_SANDBOXES
    mock_instance.destroy.assert_called_once()

    # Verify kill_all_sandboxes
    ACTIVE_SANDBOXES["sbx-102"] = {"id": "sbx-102", "instance": MagicMock()}
    ACTIVE_SANDBOXES["sbx-103"] = {"id": "sbx-103", "instance": MagicMock()}
    count = kill_all_sandboxes()
    assert count == 2
    assert len(ACTIVE_SANDBOXES) == 0


def test_sandbox_mock_execution_and_filesystem(tmp_path):
    """Test full execution paths of new tools (read, write, list, kill) using mocked OpenSandbox."""
    ACTIVE_SANDBOXES.clear()
    mock_sbx = MagicMock()
    
    # Setup command runner mock
    mock_res = MagicMock()
    mock_res.exit_code = 0
    mock_res.stdout = "Hello OpenSandbox\n"
    mock_res.stderr = ""
    mock_sbx.commands.run.return_value = mock_res

    # Setup files API mock
    mock_sbx.files.read_file.return_value = b"Hello world from remote file!"
    mock_entry = MagicMock()
    mock_entry.name = "app.py"
    mock_entry.is_dir = False
    mock_entry.size = 1024
    mock_sbx.files.list.return_value = [mock_entry]

    ACTIVE_SANDBOXES["mock-sbx-id"] = {
        "id": "mock-sbx-id",
        "image": "ubuntu:latest",
        "instance": mock_sbx,
        "status": "Running",
    }

    # 1. Test run command
    cmd_tool = SandboxRunCommandTool()
    output = cmd_tool.execute(command="echo 'Hello OpenSandbox'", sandbox_id="mock-sbx-id")
    assert "Exit code: 0" in output
    assert "Hello OpenSandbox" in output
    mock_sbx.commands.run.assert_called_with("echo 'Hello OpenSandbox'")

    # 2. Test direct write file (sandbox_write)
    write_tool = SandboxWriteFileTool()
    out_write = write_tool.execute(sandbox_id="mock-sbx-id", remote_path="/workspace/config.json", content='{"key": "value"}')
    assert "Successfully wrote" in out_write
    mock_sbx.files.write_file.assert_called_with("/workspace/config.json", '{"key": "value"}')

    # 3. Test read file back to context (sandbox_read)
    read_tool = SandboxReadFileTool()
    out_read = read_tool.execute(sandbox_id="mock-sbx-id", remote_path="/workspace/config.json")
    assert "Hello world from remote file!" in out_read
    mock_sbx.files.read_file.assert_called_with("/workspace/config.json")

    # 4. Test read file saving to local workstation disk
    local_save = tmp_path / "downloaded_config.json"
    out_save = read_tool.execute(sandbox_id="mock-sbx-id", remote_path="/workspace/config.json", local_save_path=str(local_save))
    assert "successfully extracted" in out_save
    assert local_save.exists()
    assert local_save.read_bytes() == b"Hello world from remote file!"

    # 5. Test list files (sandbox_list_files)
    list_tool = SandboxListFilesTool()
    out_list = list_tool.execute(sandbox_id="mock-sbx-id", remote_dir="/workspace")
    assert "[FILE] app.py" in out_list
    assert "1024 bytes" in out_list

    # 6. Test kill tool (sandbox_kill)
    kill_tool = SandboxKillTool()
    out_kill = kill_tool.execute(sandbox_id="mock-sbx-id")
    assert "has been terminated" in out_kill
    assert "mock-sbx-id" not in ACTIVE_SANDBOXES


def test_sandbox_create_passes_timeout_to_sdk(monkeypatch):
    """sandbox_create must forward the TTL to the SDK, not just store it."""
    import sys
    from types import ModuleType

    from corecoder.tools import sandbox as sb_mod

    # Fake a not-installed opensandbox package so the import guard passes.
    fake = ModuleType("opensandbox")
    fake.SandboxSync = type("SandboxSync", (), {"create": staticmethod(lambda **kw: MagicMock())})
    monkeypatch.setitem(sys.modules, "opensandbox", fake)

    captured = {}
    created = {}

    class FakeCreate:
        @classmethod
        def create(cls, **kwargs):
            captured.update(kwargs)
            obj = MagicMock()
            obj.id = "sbx-timeout-1"
            obj.set_timeout = MagicMock()
            created["instance"] = obj
            return obj

    monkeypatch.setattr(sb_mod, "_create_sandbox_instance", FakeCreate.create)
    ACTIVE_SANDBOXES.clear()

    tool = CreateSandboxTool()
    out = tool.execute(image="python:3.12", timeout=90)
    assert "sbx-timeout-1" in out
    assert captured.get("timeout") == 90
    assert captured.get("image") == "python:3.12"
    created["instance"].set_timeout.assert_called_once_with(90)
    assert ACTIVE_SANDBOXES["sbx-timeout-1"]["timeout"] == 90
    ACTIVE_SANDBOXES.clear()


def test_create_sandbox_instance_timeout_typeerror_fallback(monkeypatch):
    """SDKs that reject a timeout kwarg on create must fall back gracefully."""
    import sys
    from types import ModuleType

    from corecoder.tools import sandbox as sb_mod

    calls = []

    class FakeSandboxSync:
        @classmethod
        def create(cls, **kwargs):
            calls.append(kwargs)
            if "timeout" in kwargs:
                raise TypeError("__init__() got an unexpected keyword argument 'timeout'")
            return MagicMock()

    fake = ModuleType("opensandbox")
    fake.SandboxSync = FakeSandboxSync
    monkeypatch.setitem(sys.modules, "opensandbox", fake)

    result = sb_mod._create_sandbox_instance(image="ubuntu:latest", timeout=300)
    assert len(calls) == 2
    assert calls[0]["timeout"] == 300
    assert "timeout" not in calls[1]
    assert result is not None


def test_get_sandbox_reconnect_preserves_ttl(monkeypatch):
    """Re-connecting an active sandbox must keep the TTL recorded at create time."""
    import sys
    from types import ModuleType

    from corecoder.tools import sandbox as sb_mod

    mock_sbx = MagicMock()
    FakeSandboxSync = type("SandboxSync", (), {"connect": staticmethod(lambda *a, **k: mock_sbx)})
    fake = ModuleType("opensandbox")
    fake.SandboxSync = FakeSandboxSync
    monkeypatch.setitem(sys.modules, "opensandbox", fake)

    ACTIVE_SANDBOXES.clear()
    ACTIVE_SANDBOXES["sbx-reconnect"] = {
        "id": "sbx-reconnect",
        "image": "python:3.12",
        "created_at": time.time(),
        "timeout": 45,
        "instance": None,  # force reconnect path
        "status": "Running",
    }

    got = sb_mod._get_sandbox("sbx-reconnect")
    assert got is mock_sbx
    assert ACTIVE_SANDBOXES["sbx-reconnect"]["timeout"] == 45
    ACTIVE_SANDBOXES.clear()


def test_sandbox_create_retries_on_transient_failure(monkeypatch):
    """sandbox_create must retry with backoff until it succeeds."""
    import sys
    from types import ModuleType

    from corecoder.tools import sandbox as sb_mod

    fake = ModuleType("opensandbox")
    fake.SandboxSync = type("SandboxSync", (), {"create": staticmethod(lambda **kw: None)})
    monkeypatch.setitem(sys.modules, "opensandbox", fake)

    attempts = []

    def flaky_create(image, timeout, connection_config=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("temporary network blip")
        obj = MagicMock()
        obj.id = "sbx-retry-1"
        obj.set_timeout = MagicMock()
        return obj

    monkeypatch.setattr(sb_mod, "_create_sandbox_instance", flaky_create)
    monkeypatch.setattr(sb_mod.time, "sleep", lambda s: None)
    ACTIVE_SANDBOXES.clear()

    tool = CreateSandboxTool()
    out = tool.execute(image="python:3.12", timeout=90)
    assert len(attempts) == 3
    assert "sbx-retry-1" in out
    assert ACTIVE_SANDBOXES["sbx-retry-1"]["timeout"] == 90
    ACTIVE_SANDBOXES.clear()


def test_sandbox_create_reports_failure_after_all_retries(monkeypatch):
    """sandbox_create must report a clear error once all retries are exhausted."""
    import sys
    from types import ModuleType

    from corecoder.tools import sandbox as sb_mod

    fake = ModuleType("opensandbox")
    fake.SandboxSync = type("SandboxSync", (), {"create": staticmethod(lambda **kw: None)})
    monkeypatch.setitem(sys.modules, "opensandbox", fake)

    attempts = []

    def always_fail(image, timeout, connection_config=None):
        attempts.append(1)
        raise RuntimeError("scheduler full")

    monkeypatch.setattr(sb_mod, "_create_sandbox_instance", always_fail)
    monkeypatch.setattr(sb_mod.time, "sleep", lambda s: None)
    ACTIVE_SANDBOXES.clear()

    tool = CreateSandboxTool()
    out = tool.execute(image="python:3.12", timeout=90)
    assert len(attempts) == 5
    assert "[ERROR]" in out
    assert "after 5 attempts" in out
    assert "scheduler full" in out
    ACTIVE_SANDBOXES.clear()
