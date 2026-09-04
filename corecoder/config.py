"""Configuration - env vars and defaults."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv():
    """Load .env from cwd, walking up to home dir. No-op if python-dotenv missing."""
    try:
        from dotenv import load_dotenv
        # search cwd first, then parent dirs up to ~
        env_path = Path(".env")
        if not env_path.exists():
            cur = Path.cwd()
            home = Path.home()
            while cur != home and cur != cur.parent:
                candidate = cur / ".env"
                if candidate.exists():
                    env_path = candidate
                    break
                cur = cur.parent
        load_dotenv(env_path, override=False)
    except ImportError:
        pass  # python-dotenv not installed, silently skip


@dataclass
class Config:
    model: str = "gpt-5.5"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    provider: str = "openai"

    @classmethod
    def from_env(cls) -> "Config":
        # load .env if present (won't override existing env vars)
        _load_dotenv()
        # pick up common env vars automatically
        api_key = (
            os.getenv("CORECODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        return cls(
            model=os.getenv("CORECODER_MODEL", "gpt-5.5"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("CORECODER_BASE_URL"),
            max_tokens=int(os.getenv("CORECODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("CORECODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("CORECODER_MAX_CONTEXT", "128000")),
            provider=os.getenv("CORECODER_PROVIDER", "openai"),
        )


# ═══════════════════════════════════════════════════════════
#  Settings file (settings.json)
# ═══════════════════════════════════════════════════════════

SETTINGS_PATHS = [
    Path.home() / ".config" / "corecoder" / "settings.json",   # XDG-style global
    Path.home() / ".corecoder" / "settings.json",               # simple global
    Path(".corecoder") / "settings.json",                       # project-local
]


def load_settings() -> dict:
    """Load the first settings.json found from the search paths.

    Search order: ~/.config/corecoder/ → ~/.corecoder/ → .corecoder/ (cwd).
    Returns empty dict if no settings file exists.
    """
    for path in SETTINGS_PATHS:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return {}


# ═══════════════════════════════════════════════════════════
#  Skills directory
# ═══════════════════════════════════════════════════════════

SKILLS_DIRS = [
    Path("skills"),                       # project-local root (highest priority, most visible)
    Path(".corecoder") / "skills",        # project-local hidden
    Path.home() / ".corecoder" / "skills",  # global fallback
]


def get_skills_dir() -> Path:
    """Get the skills directory path.

    Returns the first existing directory from SKILLS_DIRS,
    or the project-local path if none exist.
    """
    for path in SKILLS_DIRS:
        if path.exists():
            return path
    # Return project-local path as default (will be created if needed)
    return SKILLS_DIRS[0]


# ======================================================================
#  Knowledge base directory
# ======================================================================

KNOWLEDGE_DIRS = [
    Path(".corecoder") / "knowledge",              # project-local
    Path.home() / ".corecoder" / "knowledge",      # global fallback
]


def get_knowledge_dir() -> Path:
    """Get the knowledge base directory path.

    Returns the first existing directory from KNOWLEDGE_DIRS,
    or the project-local path if none exist.
    """
    for path in KNOWLEDGE_DIRS:
        if path.exists():
            return path
    return KNOWLEDGE_DIRS[0]


# ======================================================================
#  MCP servers configuration
# ======================================================================

MCP_CONFIG_PATHS = [
    Path(".corecoder") / "mcp_servers.json",          # project-local
    Path.home() / ".corecoder" / "mcp_servers.json",  # global fallback
]


def load_mcp_config() -> dict:
    """Load MCP servers configuration from mcp_servers.json.

    Search order: .corecoder/ (cwd) → ~/.corecoder/ (home).
    Returns empty dict if no config file exists.

    Expected format:
    {
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "autoConnect": true
            }
        }
    }
    """
    for path in MCP_CONFIG_PATHS:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return {}


# ======================================================================
#  OpenSandbox configuration
# ======================================================================


def get_opensandbox_config() -> dict:
    """Get OpenSandbox connection defaults from environment variables.

    Environment variables:
        OPENSANDBOX_DOMAIN: Domain/host of OpenSandbox API server (default: 'localhost:8080' for local Docker/K8s)
        OPENSANDBOX_PROTOCOL: Protocol to communicate with server ('http' or 'https', default: 'http')
        OPENSANDBOX_API_KEY: Authentication token if required by cloud/remote gateway
    """
    _load_dotenv()
    return {
        "domain": os.getenv("OPENSANDBOX_DOMAIN", "localhost:8080"),
        "protocol": os.getenv("OPENSANDBOX_PROTOCOL", "http"),
        "api_key": os.getenv("OPENSANDBOX_API_KEY", ""),
    }

