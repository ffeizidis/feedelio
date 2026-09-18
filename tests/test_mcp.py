import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_mcp_boundary(core):
    async def check():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "feedelio.mcp_server"],
            env={**os.environ, "FEEDELIO_DATA": str(core.root)},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {
                    "search_articles",
                    "read_article",
                    "list_subscriptions",
                    "set_article_state",
                    "subscribe",
                } <= names
                result = await session.call_tool("search_articles", {"query": "anything"})
                assert not result.isError
                content = result.structuredContent or json.loads(result.content[0].text)
                assert content["total"] == 0

    asyncio.run(check())
