"""MCP tools - wrapper for MCP server tools and management commands."""

from .base import Tool
from ..mcp import MCPClientManager


class MCPTool(Tool):
    """Wrapper for an MCP server tool."""

    def __init__(self, server: str, tool_name: str, description: str,
                 parameters: dict, mcp_manager: MCPClientManager):
        self.server = server
        self.original_name = tool_name
        self.name = f"mcp__{mcp_manager.normalize_name(server)}__{mcp_manager.normalize_name(tool_name)}"
        self.description = description
        self.parameters = parameters
        self.mcp_manager = mcp_manager

    def execute(self, **kwargs) -> str:
        return self.mcp_manager._run_async(
            self.mcp_manager.call_tool(self.server, self.original_name, kwargs)
        )


class ConnectMCPTool(Tool):
    """Tool to connect to an MCP server."""

    name = "mcp_connect"
    description = "Connect to an MCP server. Provide server name and command/args for stdio, or url for HTTP/SSE."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Server name"},
            "command": {"type": "string", "description": "Command to run (for stdio transport)"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments"},
            "url": {"type": "string", "description": "Server URL (for HTTP/SSE transport)"}
        },
        "required": ["name"]
    }

    def __init__(self, mcp_manager: MCPClientManager):
        self.mcp_manager = mcp_manager

    def execute(self, name: str, command: str = None, args: list = None, url: str = None) -> str:
        config = {}
        if command:
            config["command"] = command
            if args:
                config["args"] = args
        elif url:
            config["url"] = url
        else:
            return "Error: Must provide either 'command' or 'url'"
        return self.mcp_manager._run_async(self.mcp_manager.connect(name, config))


class DisconnectMCPTool(Tool):
    """Tool to disconnect from an MCP server."""

    name = "mcp_disconnect"
    description = "Disconnect from an MCP server."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Server name"}},
        "required": ["name"]
    }

    def __init__(self, mcp_manager: MCPClientManager):
        self.mcp_manager = mcp_manager

    def execute(self, name: str) -> str:
        return self.mcp_manager._run_async(self.mcp_manager.disconnect(name))


class ListMCPToolsTool(Tool):
    """Tool to list MCP tools."""

    name = "mcp_list_tools"
    description = "List tools from connected MCP servers. Optionally specify server name."
    parameters = {
        "type": "object",
        "properties": {"server": {"type": "string", "description": "Server name (optional)"}},
        "required": []
    }

    def __init__(self, mcp_manager: MCPClientManager):
        self.mcp_manager = mcp_manager

    def execute(self, server: str = None) -> str:
        if server:
            tools = self.mcp_manager.get_server_tools(server)
            if tools is None:
                return f"Error: Server '{server}' not connected"
            if not tools:
                return f"Server '{server}' has no tools"
            lines = [f"Tools from '{server}':"]
            for tool in tools:
                lines.append(f"  - {tool['name']}: {tool['description']}")
            return '\n'.join(lines)
        else:
            servers = self.mcp_manager.list_servers()
            if not servers:
                return "No MCP servers connected"
            lines = ["Connected MCP servers:"]
            for name, count in servers.items():
                lines.append(f"  - {name}: {count} tools")
            return '\n'.join(lines)