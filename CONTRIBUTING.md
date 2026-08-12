# Contributing to decimalai-mcp

Thanks for your interest. This is a read-only MCP server: it exposes the public skill
registry to MCP clients and never writes. Keep it that way — a tool that mutates state
belongs in the SDK, not here.

## Before you open a PR

```bash
pip install -e ".[dev]"
pytest                            # all HTTP is mocked; no network access needed
python -m decimalai_mcp.server    # run it over stdio against a real client
```

## What a PR is expected to contain

- **A test.** Every HTTP interaction is mocked, so there is no excuse for an untested code
  path. Tests must not reach the network.
- **No new tool without a reason to exist.** Each tool is surface area a client has to
  understand; adding one is a product decision, not just a code change.
- **The `mcp` pin left alone** unless the PR is specifically about moving it. The cap is
  load-bearing — the server depends on FastMCP being present in the resolved version.

## Reporting bugs

Open an issue including the MCP client you are using and the exact tool call that failed.
For anything security-related see [SECURITY.md](SECURITY.md) — please do not open a public
issue.
