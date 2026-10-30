# SPDX-License-Identifier: MIT OR Apache-2.0
"""A tiny stdio MCP server for tests (needs the `mcp` package)."""

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="stub")


@server.tool()
def shout(text: str) -> str:
    """Upper-case the text."""
    return text.upper()


if __name__ == "__main__":
    server.run("stdio")
