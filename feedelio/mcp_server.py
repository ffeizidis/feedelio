"""Local stdio MCP; the agent receives access to this single user's library."""

from mcp.server.fastmcp import FastMCP

from feedelio.core import Core

mcp = FastMCP("Feedelio")


@mcp.tool()
def search_articles(query: str, unread: bool = False, limit: int = 20) -> dict:
    """Search indexed titles, article text and supplied transcripts."""
    with Core() as core:
        return core.articles(q=query, unread=unread, limit=limit)


@mcp.tool()
def read_article(article_id: str) -> dict:
    """Read an article without changing its read state."""
    with Core() as core:
        return core.article(article_id)


@mcp.tool()
def list_subscriptions() -> dict:
    """List subscriptions and their folders and unread counts."""
    with Core() as core:
        return {"feeds": core.feeds(), "folders": core.folders()}


@mcp.tool()
def set_article_state(article_id: str, read: bool | None = None, starred: bool | None = None) -> dict:
    """Change read or saved state; returns an undo token."""
    with Core() as core:
        return core.change_articles([article_id], read=read, starred=starred)


@mcp.tool()
def subscribe(url: str, folder_id: str = "inbox") -> dict:
    """Subscribe to a feed URL and queue its first refresh."""
    with Core() as core:
        return core.subscribe(url, folder_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
