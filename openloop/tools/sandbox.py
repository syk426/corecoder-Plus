"""Alibaba OpenSandbox tools — run commands, Python, and manage filesystem in isolated containers."""

import os
import shlex
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import get_opensandbox_config
from .base import Tool

DEFAULT_IMAGE = "python:3.12"
DEFAULT_TIMEOUT = 600
MAX_RESULT_LEN = 20_000
MAX_ERROR_LEN = 4_000

INVALID_SANDBOX_IDS = {
    "default",
    "sandbox1",
    "sandbox",
    "some_id",
    "new_sandbox",
    "python",
    "create_sandbox",
    "sandbox123",
    "temp",
    "sandbox-0",
    "sandbox-1",
    "sandbox_0",
    "sandbox_1",
    "new",
    "0",
    "auto",
    "default_sandbox",
    "none",
    "sandbox_12345",
    "dummy",
    "sandbox_01",
}

# Thread-safe in-memory registry of active sandboxes managed during this session
# Mapping: sandbox_id -> {"id": str, "image": str, "created_at": float, "timeout": int, "instance": SandboxSync/None, "status": str}
ACTIVE_SANDBOXES: dict[str, dict[str, Any]] = {}


def _truncate_result(result: str) -> str:
    if len(result) > MAX_RESULT_LEN:
        return result[:MAX_RESULT_LEN] + "\n[Result truncated due to length limit]"
    return result


def _looks_like_dir(path: str) -> bool:
    return path.endswith(("/", "\\")) or not os.path.splitext(path)[1]


def _extract_command_output(res: Any) -> tuple[str, str, int]:
    """Extract stdout, stderr, and exit_code from various SDK result structures or test mocks."""
    stdout = ""
    stderr = ""
    exit_code = getattr(res, "exit_code", getattr(res, "code", 0))
    if not isinstance(exit_code, int) and hasattr(exit_code, "__int__"):
        try:
            exit_code = int(exit_code)
        except Exception:
            exit_code = 0
    elif not isinstance(exit_code, int):
        exit_code = 0

    direct_out = getattr(res, "stdout", None)
    direct_err = getattr(res, "stderr", None)
    if isinstance(direct_out, (str, bytes)):
        stdout = direct_out.decode("utf-8") if isinstance(direct_out, bytes) else direct_out
    if isinstance(direct_err, (str, bytes)):
        stderr = direct_err.decode("utf-8") if isinstance(direct_err, bytes) else direct_err

    if not stdout and not stderr and hasattr(res, "logs"):
        logs = getattr(res, "logs", None)
        if logs and hasattr(logs, "stdout") and not hasattr(logs.stdout, "_mock_return_value"):
            try:
                stdout = "".join(getattr(item, "text", str(item)) for item in logs.stdout)
            except Exception:
                pass
        if logs and hasattr(logs, "stderr") and not hasattr(logs.stderr, "_mock_return_value"):
            try:
                stderr = "".join(getattr(item, "text", str(item)) for item in logs.stderr)
            except Exception:
                pass

    return stdout, stderr, exit_code



def _get_connection_config():
    """Build OpenSandbox ConnectionConfigSync from system configuration."""
    try:
        from opensandbox.config import ConnectionConfigSync
        cfg_dict = get_opensandbox_config()
        domain = cfg_dict.get("domain", "localhost:8080")
        api_key = cfg_dict.get("api_key", "")
        if api_key:
            return ConnectionConfigSync(domain=domain, api_key=api_key)
        return ConnectionConfigSync(domain=domain)
    except ImportError:
        return None


def _get_sandbox(sandbox_id: str):
    """Retrieve existing sandbox instance or connect to remote OpenSandbox."""
    if sandbox_id in INVALID_SANDBOX_IDS:
        raise ValueError(f"'{sandbox_id}' is not a valid sandbox_id. Please create a real sandbox first using sandbox_create.")

    if sandbox_id in ACTIVE_SANDBOXES and ACTIVE_SANDBOXES[sandbox_id].get("instance"):
        return ACTIVE_SANDBOXES[sandbox_id]["instance"]

    try:
        from opensandbox import SandboxSync
    except ImportError:
        raise ImportError("opensandbox package not installed. Install with: pip install opensandbox")

    cfg = _get_connection_config()
    sandbox = SandboxSync.connect(sandbox_id, connection_config=cfg)
    # Preserve the TTL recorded at create time when re-connecting an active sandbox
    stored_timeout = ACTIVE_SANDBOXES.get(sandbox_id, {}).get("timeout", DEFAULT_TIMEOUT)
    ACTIVE_SANDBOXES[sandbox_id] = {
        "id": sandbox_id,
        "image": "connected-instance",
        "created_at": time.time(),
        "timeout": stored_timeout,
        "instance": sandbox,
        "status": "Running",
    }
    return sandbox


def _create_sandbox_instance(image: str, timeout: int, connection_config=None):
    """Create an OpenSandbox container, passing the TTL where the SDK supports it.

    The SDK may not accept a `timeout` kwarg on create; fall back to the default
    TTL there and rely on _apply_timeout to push it afterwards.
    """
    from opensandbox import SandboxSync

    kwargs = {"image": image}
    if connection_config is not None:
        kwargs["connection_config"] = connection_config
    try:
        return SandboxSync.create(timeout=timeout, **kwargs)
    except TypeError as e:
        if "timeout" not in str(e):
            raise
        return SandboxSync.create(**kwargs)


def _apply_timeout(sandbox, timeout: int):
    """Best-effort TTL update: use whichever setter the SDK exposes, if any."""
    for name in ("set_timeout", "update_timeout", "set_ttl"):
        fn = getattr(sandbox, name, None)
        if callable(fn):
            try:
                fn(timeout)
                return
            except Exception:
                continue


# ======================================================================
#  Sandbox Lifecycle Manager Utilities (Used by Tools, CLI & Agent Hooks)
# ======================================================================


def get_active_sandboxes() -> list[dict[str, Any]]:
    """Return a list of summaries for all actively managed sandboxes."""
    results = []
    current_time = time.time()
    for sid, info in list(ACTIVE_SANDBOXES.items()):
        elapsed = current_time - info.get("created_at", current_time)
        remaining = max(0, info.get("timeout", DEFAULT_TIMEOUT) - elapsed)
        results.append({
            "id": sid,
            "image": info.get("image", DEFAULT_IMAGE),
            "uptime_seconds": int(elapsed),
            "remaining_seconds": int(remaining),
            "status": info.get("status", "Running"),
            "engine": "Alibaba OpenSandbox",
        })
    return results


def kill_sandbox(sandbox_id: str) -> bool:
    """Explicitly terminate and clean up a targeted sandbox instance."""
    info = ACTIVE_SANDBOXES.get(sandbox_id)
    if not info:
        return False
    instance = info.get("instance")
    try:
        if instance:
            destroy = getattr(instance, "destroy", None) or getattr(instance, "kill", None) or getattr(instance, "close", None)
            if callable(destroy):
                destroy()
    except Exception:
        pass
    finally:
        ACTIVE_SANDBOXES.pop(sandbox_id, None)
    return True


def kill_all_sandboxes() -> int:
    """Gracefully terminate all active sandboxes upon session close or REPL exit."""
    count = 0
    for sid in list(ACTIVE_SANDBOXES.keys()):
        if kill_sandbox(sid):
            count += 1
    return count


# ======================================================================
#  Tool Implementations (9 Atomic Tools)
# ======================================================================


class CreateSandboxTool(Tool):
    """Create an isolated Linux container sandbox for running code and commands."""

    name = "sandbox_create"
    description = "Create an isolated container sandbox via OpenSandbox. Returns a sandbox_id to use with other sandbox tools. Timeout defaults to 600 seconds."
    parameters = {
        "type": "object",
        "properties": {
            "image": {
                "type": "string",
                "description": f"Container base image (default: '{DEFAULT_IMAGE}')",
            },
            "timeout": {
                "type": "integer",
                "description": "Sandbox timeout in seconds (default 600)",
            },
        },
        "required": [],
    }

    def execute(self, image: str = DEFAULT_IMAGE, timeout: int = DEFAULT_TIMEOUT) -> str:
        try:
            from opensandbox import SandboxSync
        except ImportError:
            return "[ERROR]: opensandbox package not installed. Install with: pip install opensandbox"

        timeout = min(timeout, DEFAULT_TIMEOUT)
        cfg = _get_connection_config()
        max_retries = 5
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                sandbox = _create_sandbox_instance(image=image, timeout=timeout, connection_config=cfg)
                _apply_timeout(sandbox, timeout)
                sid = getattr(sandbox, "id", None) or getattr(sandbox, "sandbox_id", None) or str(uuid.uuid4())
                sid_str = str(sid)

                ACTIVE_SANDBOXES[sid_str] = {
                    "id": sid_str,
                    "image": image,
                    "created_at": time.time(),
                    "timeout": timeout,
                    "instance": sandbox,
                    "status": "Running",
                }
                return f"Sandbox created with sandbox_id: {sid_str}"
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(attempt ** 2)
        return f"[ERROR]: Failed to create OpenSandbox container (image='{image}') after {max_retries} attempts: {str(last_error)[:MAX_ERROR_LEN]}"


class SandboxRunCommandTool(Tool):
    """Run a shell command in the sandbox."""

    name = "sandbox_run_command"
    description = "Run a shell command in an existing sandbox. Requires a valid sandbox_id from sandbox_create."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create"},
        },
        "required": ["command", "sandbox_id"],
    }

    def execute(self, command: str, sandbox_id: str) -> str:
        try:
            sandbox = _get_sandbox(sandbox_id)
            result = sandbox.commands.run(command)
            stdout, stderr, exit_code = _extract_command_output(result)
            output = f"Exit code: {exit_code}"
            if stdout:
                output += f"\n--- stdout ---\n{stdout.strip()}"
            if stderr:
                output += f"\n--- stderr ---\n{stderr.strip()}"
            return _truncate_result(output)
        except Exception as e:
            return f"[ERROR]: Failed to run command in sandbox {sandbox_id}: {str(e)[:MAX_ERROR_LEN]}"


class SandboxRunPythonTool(Tool):
    """Execute Python code in the sandbox."""

    name = "sandbox_run_python"
    description = "Execute Python code in the sandbox. If sandbox_id is empty, creates a stateless temporary sandbox that is destroyed after execution."
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python script code to execute"},
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create (optional, leave empty for stateless execution)"},
        },
        "required": ["code"],
    }

    def execute(self, code: str, sandbox_id: str = "") -> str:
        stateless = not sandbox_id or sandbox_id in INVALID_SANDBOX_IDS
        sandbox = None
        sid = sandbox_id
        try:
            if stateless:
                try:
                    from opensandbox import SandboxSync
                except ImportError:
                    return "[ERROR]: opensandbox package not installed. Install with: pip install opensandbox"
                cfg = _get_connection_config()
                sandbox = SandboxSync.create(image=DEFAULT_IMAGE, connection_config=cfg)
            else:
                sandbox = _get_sandbox(sid)

            # Execute Python safely via temporary file script in container
            script_filename = f"/tmp/script_{uuid.uuid4().hex[:8]}.py"
            if hasattr(sandbox, "files") and hasattr(sandbox.files, "write_file"):
                sandbox.files.write_file(script_filename, code)
            elif hasattr(sandbox, "files") and hasattr(sandbox.files, "write"):
                sandbox.files.write(script_filename, code.encode("utf-8"))
            else:
                # Fallback via shell escaped heredoc if file API is missing
                safe_code = shlex.quote(code)
                sandbox.commands.run(f"echo {safe_code} > {script_filename}")

            res = sandbox.commands.run(f"python3 {script_filename}")
            
            # Clean up temporary file
            try:
                sandbox.commands.run(f"rm -f {script_filename}")
            except Exception:
                pass

            stdout, stderr, exit_code = _extract_command_output(res)
            output = f"Execution exit code: {exit_code}"
            if stdout:
                output += f"\n{stdout.strip()}"
            if stderr:
                output += f"\n[stderr]\n{stderr.strip()}"
            return _truncate_result(output)
        except Exception as e:
            return f"[ERROR]: Failed to execute Python in sandbox: {str(e)[:MAX_ERROR_LEN]}"
        finally:
            if stateless and sandbox:
                try:
                    destroy = getattr(sandbox, "destroy", None) or getattr(sandbox, "kill", None) or getattr(sandbox, "close", None)
                    if callable(destroy):
                        destroy()
                except Exception:
                    pass


class SandboxUploadFileTool(Tool):
    """Upload a local workstation file to the remote sandbox."""

    name = "sandbox_upload"
    description = "Upload a local host file into the sandbox filesystem."
    parameters = {
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create"},
            "local_file_path": {"type": "string", "description": "Path to the local file on host workstation"},
            "sandbox_file_path": {"type": "string", "description": "Remote target path in sandbox (default: '.')"},
        },
        "required": ["sandbox_id", "local_file_path"],
    }

    def execute(self, sandbox_id: str, local_file_path: str, sandbox_file_path: str = ".") -> str:
        try:
            if not os.path.exists(local_file_path):
                return f"[ERROR]: Local file does not exist: {local_file_path}"
            if not os.path.isfile(local_file_path):
                return f"[ERROR]: Path is not a file: {local_file_path}"

            sandbox = _get_sandbox(sandbox_id)
            local_base = os.path.basename(local_file_path)
            if _looks_like_dir(sandbox_file_path):
                remote_path = os.path.normpath(os.path.join(sandbox_file_path, local_base))
            else:
                remote_path = os.path.normpath(sandbox_file_path)

            parent_dir = os.path.dirname(remote_path)
            if parent_dir and parent_dir != "/":
                sandbox.commands.run(f"mkdir -p {shlex.quote(parent_dir)}")

            with open(local_file_path, "rb") as f:
                content = f.read()

            if hasattr(sandbox.files, "write_file"):
                sandbox.files.write_file(remote_path, content)
            elif hasattr(sandbox.files, "write"):
                sandbox.files.write(remote_path, content)
            else:
                return "[ERROR]: Sandbox client does not support file write API."

            return f"File successfully uploaded to sandbox at: {remote_path}"
        except Exception as e:
            return f"[ERROR]: Failed to upload file to sandbox {sandbox_id}: {str(e)[:MAX_ERROR_LEN]}"


class SandboxDownloadFileTool(Tool):
    """Download an internet file into the sandbox via wget."""

    name = "sandbox_download"
    description = "Download a file from an internet URL into the sandbox container using wget."
    parameters = {
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create"},
            "url": {"type": "string", "description": "Internet URL to download"},
            "sandbox_file_path": {"type": "string", "description": "Remote path in sandbox (default: '.')"},
        },
        "required": ["sandbox_id", "url"],
    }

    def execute(self, sandbox_id: str, url: str, sandbox_file_path: str = ".") -> str:
        try:
            sandbox = _get_sandbox(sandbox_id)
            parsed_url = urlparse(url)
            basename = os.path.basename(parsed_url.path) or "downloaded_file"
            if "?" in basename:
                basename = basename.split("?")[0]

            if _looks_like_dir(sandbox_file_path):
                remote_path = os.path.join(sandbox_file_path, basename)
            else:
                remote_path = sandbox_file_path
            remote_path = os.path.normpath(remote_path)

            parent_dir = os.path.dirname(remote_path)
            if parent_dir and parent_dir != "/":
                sandbox.commands.run(f"mkdir -p {shlex.quote(parent_dir)}")

            safe_url = shlex.quote(url)
            safe_path = shlex.quote(remote_path)
            res = sandbox.commands.run(f"wget {safe_url} -O {safe_path}")
            exit_code = getattr(res, "exit_code", getattr(res, "code", 0))
            if exit_code == 0:
                return f"Successfully downloaded URL to {remote_path} in sandbox {sandbox_id}"
            return f"[ERROR]: wget command failed with exit code {exit_code}."
        except Exception as e:
            return f"[ERROR]: Failed to download URL in sandbox {sandbox_id}: {str(e)[:MAX_ERROR_LEN]}"


class SandboxReadFileTool(Tool):
    """Read file content from the remote sandbox container or extract it to host disk."""

    name = "sandbox_read"
    description = "Read a file from the sandbox container filesystem back into context, or extract it to a local workstation disk path."
    parameters = {
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create"},
            "remote_path": {"type": "string", "description": "Absolute file path inside the sandbox container to read"},
            "local_save_path": {"type": "string", "description": "Optional local workstation file path to save the extracted file (useful for generated images, plots, or archives)"},
        },
        "required": ["sandbox_id", "remote_path"],
    }

    def execute(self, sandbox_id: str, remote_path: str, local_save_path: str = "") -> str:
        try:
            sandbox = _get_sandbox(sandbox_id)
            content = None
            if hasattr(sandbox.files, "read_file"):
                content = sandbox.files.read_file(remote_path)
            elif hasattr(sandbox.files, "read"):
                content = sandbox.files.read(remote_path)
            else:
                # Fallback to cat via command if SDK file read is unavailable
                res = sandbox.commands.run(f"cat {shlex.quote(remote_path)}")
                stdout, _, exit_code = _extract_command_output(res)
                if exit_code != 0:
                    return f"[ERROR]: File not found or unreadable at {remote_path}"
                content = stdout

            if content is None:
                return f"[ERROR]: File not found or could not be read from sandbox path: {remote_path}"

            # Convert to binary if saving to local disk
            if local_save_path:
                save_dir = os.path.dirname(os.path.abspath(local_save_path))
                if save_dir:
                    os.makedirs(save_dir, exist_ok=True)
                write_mode = "wb" if isinstance(content, bytes) else "w"
                encoding = None if isinstance(content, bytes) else "utf-8"
                with open(local_save_path, write_mode, encoding=encoding) as f:
                    f.write(content)
                return f"File successfully extracted from sandbox ({remote_path}) to local disk at: {os.path.abspath(local_save_path)}"

            # Return text preview for agent context
            text_str = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            return _truncate_result(f"Content of {remote_path} in sandbox {sandbox_id}:\n{text_str}")
        except Exception as e:
            return f"[ERROR]: Failed to read file {remote_path} from sandbox {sandbox_id}: {str(e)[:MAX_ERROR_LEN]}"


class SandboxWriteFileTool(Tool):
    """Directly write text/string content into a file in the sandbox filesystem."""

    name = "sandbox_write"
    description = "Directly write text string or code into a remote file path inside the sandbox container."
    parameters = {
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create"},
            "remote_path": {"type": "string", "description": "Target absolute file path inside the sandbox to write"},
            "content": {"type": "string", "description": "Text content or source code to write into the file"},
        },
        "required": ["sandbox_id", "remote_path", "content"],
    }

    def execute(self, sandbox_id: str, remote_path: str, content: str) -> str:
        try:
            sandbox = _get_sandbox(sandbox_id)
            parent_dir = os.path.dirname(os.path.normpath(remote_path))
            if parent_dir and parent_dir != "/":
                sandbox.commands.run(f"mkdir -p {shlex.quote(parent_dir)}")

            if hasattr(sandbox.files, "write_file"):
                sandbox.files.write_file(remote_path, content)
            elif hasattr(sandbox.files, "write"):
                sandbox.files.write(remote_path, content.encode("utf-8") if isinstance(content, str) else content)
            else:
                return "[ERROR]: Sandbox SDK does not support direct file writing."

            return f"Successfully wrote {len(content)} characters directly to {remote_path} in sandbox {sandbox_id}"
        except Exception as e:
            return f"[ERROR]: Failed to write directly to {remote_path} in sandbox {sandbox_id}: {str(e)[:MAX_ERROR_LEN]}"


class SandboxListFilesTool(Tool):
    """List directory trees and files cleanly inside the sandbox container."""

    name = "sandbox_list_files"
    description = "List file names, directories, and structure inside a target folder in the remote sandbox container."
    parameters = {
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string", "description": "The sandbox_id from sandbox_create"},
            "remote_dir": {"type": "string", "description": "Remote directory path to list (default: '/work' or '.')"},
        },
        "required": ["sandbox_id"],
    }

    def execute(self, sandbox_id: str, remote_dir: str = ".") -> str:
        try:
            sandbox = _get_sandbox(sandbox_id)
            if hasattr(sandbox.files, "list"):
                entries = sandbox.files.list(remote_dir)
                out = f"Directory contents of '{remote_dir}' in sandbox {sandbox_id}:\n"
                for entry in entries:
                    name = getattr(entry, "name", str(entry))
                    is_dir = getattr(entry, "is_dir", False)
                    size = getattr(entry, "size", 0)
                    kind = "[DIR] " if is_dir else "[FILE]"
                    out += f"  {kind} {name} (size: {size} bytes)\n"
                return _truncate_result(out.strip())
            
            # Fallback cleanly via standard ls formatting
            res = sandbox.commands.run(f"ls -lhA {shlex.quote(remote_dir)}")
            stdout, _, exit_code = _extract_command_output(res)
            if exit_code != 0:
                return f"[ERROR]: Cannot access directory '{remote_dir}' in sandbox {sandbox_id}."
            return _truncate_result(f"Directory listing of '{remote_dir}' in sandbox {sandbox_id}:\n{stdout.strip()}")
        except Exception as e:
            return f"[ERROR]: Failed to list directory '{remote_dir}' in sandbox {sandbox_id}: {str(e)[:MAX_ERROR_LEN]}"


class SandboxKillTool(Tool):
    """Explicitly kill and reclaim compute resources of an active sandbox container."""

    name = "sandbox_kill"
    description = "Explicitly kill an active sandbox instance immediately to release system resources before TTL expires."
    parameters = {
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string", "description": "The sandbox_id of the running container to destroy"},
        },
        "required": ["sandbox_id"],
    }

    def execute(self, sandbox_id: str) -> str:
        if sandbox_id not in ACTIVE_SANDBOXES:
            return f"[WARN]: Sandbox '{sandbox_id}' is not found in active registries or has already terminated."
        success = kill_sandbox(sandbox_id)
        if success:
            return f"Sandbox '{sandbox_id}' has been terminated and removed from active management."
        return f"[ERROR]: Could not terminate sandbox '{sandbox_id}'."
