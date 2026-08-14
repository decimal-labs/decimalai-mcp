# Releasing decimalai-mcp

How a new version of the `decimalai-mcp` package reaches PyPI. Releases are cut by maintainers with
push access; this file documents the process so a contributor can see what a change has to survive.

> **PyPI is append-only.** A version number can never be reused, overwritten, or re-uploaded — even after
> you "delete" a release. If you ship a mistake, the only remedy is a *new* version. Treat the publish as
> irreversible. That matters more here than in the sibling packages, because the **`mcp-name` marker in
> `README.md` is what the official MCP registry reads to verify PyPI ownership** — a wrong marker costs a
> whole version to fix.

## How publishing works

Publishing goes through **[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)** (OIDC), so no
long-lived credential has to exist in CI: a *published GitHub Release* triggers `.github/workflows/publish.yml`,
which runs the tests, verifies the version and the `mcp-name` marker, builds, and uploads via OIDC. The
`id-token: write` permission is granted to the publish job alone — the test job installs every transitive dev
dependency, and that third-party install-time code must not be able to mint a publishing credential.

This requires one piece of setup on the PyPI side: the Trusted Publisher has to be attached under
PyPI → *Manage* → *Publishing*, matching exactly what the workflow declares — project `decimalai-mcp`, owner
`decimal-labs`, repo `decimalai-mcp`, workflow `publish.yml`, environment `pypi`. A mapping that is absent or
differs in any field (including a renamed workflow file) lets the whole run go green and then fails at the
upload step, after the tag already exists.

| Gate | Where | Checks | Blocks the release? |
|---|---|---|---|
| Tests | CI — `publish.yml` `test` job | unit + contract tests on Python 3.10–3.13 | yes (before the upload step) |
| Version ↔ tag match | CI — `publish` job | `pyproject.toml` version == `v<tag>` | yes |
| `mcp-name` marker | CI — `publish` job | `README.md` contains `mcp-name: <server.json name>` | yes |
| Live registry smoke | **Local — see below** | the three tools against production | yes — run it yourself first |

Unlike `decimalai`, there is no live-LLM gate: this server makes **no model calls**. It is a read-only HTTP
client over the public registry API, so the meaningful pre-release check is that the three tools still return
sane output against production.

## Versioning model

The version lives in **several places that must match**:

| Number | Lives in | Kept in step by |
|---|---|---|
| `version` | `pyproject.toml` | CI — asserted against the release tag |
| `__version__` | `decimalai_mcp/__init__.py` | by hand |
| `User-Agent` string | `decimalai_mcp/api.py` | by hand |
| `version` and `packages[0].version` | `server.json` | by hand |

Bump **all** of them to the same SemVer string. Only the `pyproject.toml` value is machine-checked, so
the others are the ones that drift — the `server.json` pair in particular, because it is read at MCP
registry submission time rather than at release time, and a stale value there tells clients to install
an older release than the one you just shipped. (That is not hypothetical: at 0.1.1 both `server.json`
files and the `api.py` User-Agent were still on 0.1.0.)

Check the whole set in one shot. Take the version from `pyproject.toml` (the one CI asserts) and
require **four** hits in the hand-maintained sites — `__init__.py` 1, `api.py` 1, and 2 in
`server.json`. A count other than four means a site drifted or a new one appeared (add it to the
table above). *(This was six while a second `server.io-github.json` existed; that file was removed
when the server moved to a single MCP namespace, taking two hits with it.)*

```bash
V=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
git grep -nF "$V" -- decimalai_mcp/__init__.py decimalai_mcp/api.py server.json
```

## Release steps

```bash
# 1. Bump EVERY version site in the table above, commit — all five files:
#    pyproject.toml:            version = "0.2.0"
#    decimalai_mcp/__init__.py: __version__ = "0.2.0"
#    decimalai_mcp/api.py:      User-Agent "decimalai-mcp/0.2.0"
#    server.json: "version" AND packages[0].version
#    Then re-run the six-hit grep above.

# 2. Tests green locally, on the pinned mcp.
pip install -e ".[dev]" && pytest tests/ -v

# 3. Live smoke against PRODUCTION — the three tools must return real data.
python - <<'PY'
from decimalai_mcp import api
assert "SkillScore" in api.search_skills("pdf extraction", limit=3)
assert "playwright-cli" in api.get_skill("playwright-cli")
assert api.get_leaderboard(limit=3)
print("live smoke OK")
PY

# 4. The cap is load-bearing — confirm the resolved mcp still has FastMCP.
python -c "from mcp.server.fastmcp import FastMCP; import decimalai_mcp.server; print('FastMCP OK')"

# 5. Cut the Release. CI does the rest — tests, version + marker checks, then the upload.
gh release create v0.2.0 --title "v0.2.0" --notes "..."
```

Steps 1–4 are worth running on any change that touches the package, release or not — step 3 is the only
check that catches the registry API changing shape underneath the server.
