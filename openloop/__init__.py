"""OpenLoop - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.1.0"

from openloop.agent import Agent
from openloop.llm import LLM
from openloop.config import Config
from openloop.tools import ALL_TOOLS

__all__ = ["Agent", "LLM", "Config", "ALL_TOOLS", "__version__"]
