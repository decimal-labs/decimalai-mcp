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

import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import __version__, api

# Resolve FastMCP's own settings model BEFORE the first FastMCP(...) call.
#
# mcp 1.x declares `Settings.lifespan: Callable[[FastMCP[...]], ...] | None` in the
# same module that defines FastMCP, under `from __future__ import annotations` — so
# the annotation is a forward reference the model is never rebuilt for. pydantic-
# settings 2.15 started warning about exactly that, and because FastMCP is built at
# import time the warning printed on EVERY startup, on stderr, which for a stdio MCP
# server is the channel clients show the user:
#   IncompleteFieldDefinitionWarning: Field 'lifespan' has an incomplete definition:
#   its annotation contains an unresolved forward reference …
# `model_rebuild()` is the fix the warning itself prescribes: by the time this module
# runs, FastMCP is fully defined, so the reference resolves. Not a filterwarnings
# suppression — the annotation genuinely becomes complete. Belt-and-braces guarded so
# an SDK that moves/renames `Settings` degrades to the old (warning) behaviour rather
# than failing at import. Harmless on pydantic-settings <2.15, which never warned.
try:  # pragma: no cover - depends on the installed mcp/pydantic-settings pair
    from mcp.server.fastmcp.server import Settings as _FastMCPSettings

    _FastMCPSettings.model_rebuild()
except Exception:  # noqa: BLE001 - never let a cosmetic fix break startup
    pass

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
        slug: The skill's `url_slug` from search results — NOT its `name`. A
            registry name may be namespaced (`owner/skill`), and a slash cannot
            survive a single URL path segment, so a namespaced name 404s here.
            `url_slug` is the slash-free identifier minted for exactly this
            (`owner/skill` -> `owner-skill`); for a plain un-namespaced name the
            two are identical. The skill's UUID `id` also works.
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


USAGE = f"""\
decimalai-mcp {__version__} — MCP server for the DecimalAI skills registry.

This is a Model Context Protocol server, not an interactive CLI. Run with no
arguments and it speaks MCP over stdio (stdin/stdout) and stays running until
the client closes the stream — so start it from an MCP client, not by hand.

Tools (all read-only, all against the public registry):
  search_skills     search the registry
  get_skill         one skill's trust/scan status, benchmark evidence, body
  get_leaderboard   the ranked effectiveness leaderboard

Environment:
  DECIMAL_API_KEY   Optional (dai_sk_...). Every tool works anonymously without
                    it; the key only adds per-org enrichment, such as
                    `installed_as` — which skills your org already installed.
  DECIMAL_API_URL   Optional base URL override (default https://api.decimal.ai).

Add to Claude Code:
  claude mcp add decimalai -- uvx decimalai-mcp

Options:
  -h, --help        Show this message and exit.
  -V, --version     Show the version and exit.
"""


def main(argv: Optional[list[str]] = None) -> None:
    """Console-script entry point.

    With NO arguments this serves MCP over stdio — the only mode that matters in
    production, and deliberately byte-for-byte what it always did. The flags exist
    because a bare `decimalai-mcp --help` used to fall straight through to
    `mcp.run()`: the server would sit there waiting on stdin, print nothing, and
    exit 0 when the pipe closed. Anyone probing a fresh install read that as "the
    command works but has no help", with no way to tell a healthy install from a
    broken one.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        mcp.run()
        return
    if args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return
    if args[0] in ("-V", "--version", "-v"):
        print(f"decimalai-mcp {__version__}")
        return
    # Anything else is a typo. Say so instead of starting a server the caller did
    # not ask for — no launcher (README, smithery.yaml, claude mcp add) passes
    # arguments, so an unrecognised one is never a live client's doing.
    print(f"decimalai-mcp: unrecognized argument {args[0]!r}\n", file=sys.stderr)
    print(USAGE, end="", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
