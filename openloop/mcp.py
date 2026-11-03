"""MCP client manager - handles connections to MCP servers and tool discovery."""

import asyncio
import re
from typing import Any


class MCPClientManager:
    """Manages MCP server connections and tool discovery."""

    def __init__(self):
        self.clients: dict[str, Any] = {}
        self.contexts: dict[str, tuple] = {}
        self.tools_cache: dict[str, list] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_async(self, coro):
        loop = self._get_loop()
        return loop.run_until_complete(coro)

    async def connect(self, name: str, config: dict) -> str:
        if name in self.clients:
            return f"MCP server '{name}' already connected"

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            if "command" in config:
                server_params = StdioServerParameters(
                    command=config["command"],
                    args=config.get("args", []),
                    env=config.get("env")
                )
                ctx = stdio_client(server_params)
                read, write = await ctx.__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()
                self.clients[name] = session
                self.contexts[name] = (ctx, session)

            elif "url" in config:
                if config.get("type") == "sse":
                    from mcp.client.sse import sse_client
                    ctx = sse_client(config["url"], headers=config.get("headers", {}))
                else:
                    try:
                        from mcp.client.streamable_http import streamablehttp_client
                        ctx = streamablehttp_client(config["url"], headers=config.get("headers", {}))
                    except ImportError:
                        return "Error: streamable-http transport not available in this MCP version"
                read, write = await ctx.__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()
                self.clients[name] = session
                self.contexts[name] = (ctx, session)
            else:
                return f"Error: Invalid config for '{name}'. Need 'command' or 'url'"

            tools_result = await self.clients[name].list_tools()
            self.tools_cache[name] = tools_result.tools
            return f"Connected to MCP server '{name}' with {len(tools_result.tools)} tools"

        except Exception as e:
            return f"Error connecting to '{name}': {e}"

    async def disconnect(self, name: str) -> str:
        if name not in self.clients:
            return f"MCP server '{name}' not connected"
        try:
            ctx, session = self.contexts[name]
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)
            del self.clients[name]
            del self.contexts[name]
            del self.tools_cache[name]
            return f"Disconnected from MCP server '{name}'"
        except Exception as e:
            return f"Error disconnecting from '{name}': {e}"

    @staticmethod
    def normalize_name(name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_-]', '_', name)

    def get_all_tools(self) -> list[dict]:
        all_tools = []
        for server_name, tools in self.tools_cache.items():
            safe_server = self.normalize_name(server_name)
            for tool in tools:
                safe_tool = self.normalize_name(tool.name)
                all_tools.append({
                    "server": server_name,
                    "original_name": tool.name,
                    "name": f"mcp__{safe_server}__{safe_tool}",
                    "description": tool.description,
                    "inputSchema": tool.inputSchema
                })
        return all_tools

    async def call_tool(self, server: str, tool_name: str, args: dict) -> str:
        if server not in self.clients:
            return f"Error: MCP server '{server}' not connected"
        try:
            result = await self.clients[server].call_tool(tool_name, args)
            if hasattr(result, 'content') and result.content:
                texts = [item.text for item in result.content if hasattr(item, 'text')]
                return '\n'.join(texts) if texts else str(result)
            return str(result)
        except Exception as e:
            return f"Error calling tool '{tool_name}': {e}"

    def list_servers(self) -> dict[str, int]:
        return {name: len(tools) for name, tools in self.tools_cache.items()}

    def get_server_tools(self, server: str) -> list[dict] | None:
        if server not in self.tools_cache:
            return None
        return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in self.tools_cache[server]]