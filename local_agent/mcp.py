"""MCP stdio tools subset; pinned to the 2025-11-25 lifecycle.
Not a full MCP SDK: no HTTP, OAuth, sampling, elicitation, prompts or resources.
"""
from __future__ import annotations
from pathlib import Path
from .rpc import RPCError, StdioRPC

VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

class MCPClient:
    def __init__(self, command: list[str], *, cwd: Path, timeout: float = 30, env=None):
        self.rpc = StdioRPC(command, cwd=cwd, timeout=timeout, env=env)
        try:
            info = self.rpc.request("initialize", {
                "protocolVersion": VERSIONS[0], "capabilities": {},
                "clientInfo": {"name": "ncnn-local-agent", "version": "0.1.0"}})
            if info.get("protocolVersion") not in VERSIONS:
                raise RPCError("Unsupported MCP protocol version")
            if "tools" not in info.get("capabilities", {}):
                raise RPCError("Server did not advertise tools capability")
            self.info = info
            self.rpc.notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def list_tools(self) -> list[dict]:
        tools, cursor, seen = [], None, set()
        for _ in range(64):
            result = self.rpc.request("tools/list", {"cursor": cursor} if cursor else {})
            batch = result.get("tools")
            if not isinstance(batch, list):
                raise RPCError("Malformed tools/list response")
            for tool in batch:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not isinstance(tool.get("inputSchema"), dict):
                    raise RPCError("Malformed MCP tool schema")
                if any(t["name"] == tool["name"] for t in tools):
                    raise RPCError("Duplicate MCP tool name")
                tools.append(tool)
            if len(tools) > 256:
                raise RPCError("Too many tools")
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if not isinstance(cursor, str) or cursor in seen:
                raise RPCError("Invalid/repeated MCP pagination cursor")
            seen.add(cursor)
        raise RPCError("MCP pagination limit exceeded")

    def call(self, name: str, arguments: dict) -> dict:
        result = self.rpc.request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise RPCError("Malformed tools/call response")
        return result

    def close(self):
        self.rpc.close()
