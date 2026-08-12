# decimalai-mcp

MCP server for the [DecimalAI](https://decimal.ai) skills registry — the registry that ranks agent skills by **measured effectiveness** (verified A/B benchmarks, live pass rates, AI rater scores), not download counts.

<!-- mcp-name: ai.decimal/registry -->

Gives any MCP client (Claude Desktop, Claude Code, Cursor, …) three read-only tools:

| Tool | What it does |
|---|---|
| `search_skills(query, category?, sort?, limit?)` | Hybrid keyword/semantic search over the public registry |
| `get_skill(slug)` | Full record: trust & safety-scan status, verified benchmark lift, SkillScore, ratings, SKILL.md body |
| `get_leaderboard(sort?, category?, window_days?, limit?)` | Ranked leaderboard: `skill_score`, `biggest_improvement`, `efficiency`, `top_rated` |

**No API key required** — all three tools read public registry endpoints. If you set `DECIMAL_API_KEY` (from [app.decimal.ai/settings](https://app.decimal.ai/settings)), the same tools additionally show which skills your org has already installed (`installed_as`).

## Install

```bash
pip install decimalai-mcp
# or, no install needed at config time:
uvx decimalai-mcp
```

Requires Python 3.10+.

## Claude Code

```bash
claude mcp add decimalai -- uvx decimalai-mcp
# with an API key:
claude mcp add decimalai -e DECIMAL_API_KEY=dai_sk_... -- uvx decimalai-mcp
```

## Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "decimalai": {
      "command": "uvx",
      "args": ["decimalai-mcp"],
      "env": {
        "DECIMAL_API_KEY": "dai_sk_optional"
      }
    }
  }
}
```

Omit the `env` block entirely for anonymous read-only access.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `DECIMAL_API_KEY` | *(unset)* | Optional. Unlocks per-org enrichment (e.g. `installed_as`) on the same public endpoints. |
| `DECIMAL_API_URL` | `https://api.decimal.ai` | Point at a self-hosted / local backend. |

## Why no `check_manifest_impact` tool?

The manifest-impact endpoint (`POST /api/v1/regression-check`) is authenticated on the platform — it analyzes **your org's** production traces against a candidate manifest, so there is no public variant to expose. This server is deliberately a read-only, key-optional public-registry surface. If demand shows up, an authed `check_manifest_impact` (requiring `DECIMAL_API_KEY`) is a natural v0.2 addition; the [regression-check GitHub Action](https://github.com/decimal-labs/regression-check) covers the CI use-case today.

## Endpoints used (all public)

- `GET /api/v1/registry/skills` — browse/search
- `GET /api/v1/registry/skills/{slug}` — detail
- `GET /api/v1/registry/leaderboard` — ranked leaderboard (`category` filtering falls back to the browse endpoint's documented `view=ranks` mode, because the leaderboard endpoint is uncategorized)

## Development

```bash
pip install -e ".[dev]"
pytest              # all HTTP mocked; no network
python -m decimalai_mcp.server   # run over stdio
```

Run `pytest` yourself before opening a PR. CI also asserts that the pinned `mcp<2` still provides `FastMCP`, which the mocked tests do not cover — run that one too:

```bash
python -c "from mcp.server.fastmcp import FastMCP; import decimalai_mcp.server"
```

Releases are cut from a published GitHub Release — see
[`RELEASING.md`](https://github.com/decimal-labs/decimalai-mcp/blob/main/RELEASING.md) for the gates a
change has to pass and the version strings that must move together.

## License

MIT
