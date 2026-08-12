"""MCP server entry point.

Written against the FastMCP interface of the official ``mcp`` Python SDK
(``mcp>=1.2,<2``). All tool logic lives in :mod:`decimalai_mcp.api` so it
is unit-testable without the ``mcp`` package installed.

Run directly (stdio transport, the default for Claude Desktop / Claude
Code)::

    decimalai-mcp
    # or: python -m decimalai_mcp.server
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import api
from . import __version__

mcp = FastMCP(
    "decimalai",
    instructions=(
        "Read-only access to the DecimalAI public skills registry "
        "(https://app.decimal.ai/skills). Skills are ranked by measured "
        "effectiveness (verified A/B benchmarks, live pass rates, AI rater), "
        "not download counts. No API key needed; set DECIMAL_API_KEY to see "
        "which skills your org already installed."
    ),
)

# Report OUR version in `initialize`, not the SDK's.
#
# FastMCP's constructor takes no `version` on mcp 1.x, and the low-level Server it
# builds defaults to the installed `mcp` library version — so serverInfo answered
# {name: "decimalai", version: "1.29.0"}. Clients display that verbatim, meaning a
# user could not tell which decimalai-mcp they were talking to, and a bug report
# would carry the SDK's number instead of ours. Set it on the underlying server,
# which is the only seam mcp 1.x exposes. Guarded so a future SDK that renames the
# attribute degrades to the old behaviour rather than failing at import.
if hasattr(mcp, "_mcp_server"):
    mcp._mcp_server.version = __version__


@mcp.tool()
def search_skills(
    query: str,
    category: Optional[str] = None,
    sort: str = "recommended",
    limit: int = 10,
) -> str:
    """Search the public DecimalAI skills registry.

    Args:
        query: Keyword or natural-language query (hybrid keyword/semantic search).
        category: Optional category filter (e.g. "retrieval", "code-review").
        sort: recommended (SkillScore v2, default) | lift | popular | top_rated |
            efficiency | rating | recent.
        limit: Max results (1-50).
    """
    return api.search_skills(query, category=category, sort=sort, limit=limit)


@mcp.tool()
def get_skill(slug: str) -> str:
    """Get one skill's full record: trust/safety-scan status, verified benchmark
    evidence (lift vs no-skill baseline), SkillScore, ratings, and the SKILL.md body.

    Args:
        slug: The skill's registry name (the `name` field from search results).
    """
    return api.get_skill(slug)


@mcp.tool()
def get_leaderboard(
    sort: str = "biggest_improvement",
    category: Optional[str] = None,
    window_days: int = 30,
    limit: int = 10,
) -> str:
    """Ranked skills leaderboard.

    Args:
        sort: skill_score | biggest_improvement | efficiency | top_rated.
        category: Optional category filter (uses the registry's ranked browse
            view — the dedicated leaderboard endpoint is uncategorized).
        window_days: Ranking window in days (1-3650; ignored for skill_score).
        limit: Max entries (1-50).
    """
    return api.get_leaderboard(
        sort=sort, category=category, window_days=window_days, limit=limit
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
