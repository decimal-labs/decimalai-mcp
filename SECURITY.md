# Security Policy

This repository is an MCP server. It is published on PyPI as **`decimalai-mcp`**, and listed in the
MCP registry as **`ai.decimal/registry`** (and mirrored by directories such as Smithery and Glama).
It runs locally, inside an MCP client — Claude Desktop, Claude Code, Cursor — over stdio, and its
tool results are read straight into a model's context. A report may concern this source, a published
package, or a registry listing; say which if you know.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Two ways to reach us, either is fine:

- **GitHub private vulnerability reporting** — **Security → Report a vulnerability** on this
  repository. That opens a private advisory only maintainers can see.
- **Email** — [hello@decimal.ai](mailto:hello@decimal.ai). A PGP key is available on request if you
  would rather not send details in cleartext.

Include what you have: what you found, how to reproduce it, the `decimalai-mcp` version, which client
you ran it in, and what an attacker could actually do with it.

## Scope

The server exposes three read-only tools (`search_skills`, `get_skill`, `get_leaderboard`) against
public registry endpoints, plus optional per-org enrichment when `DECIMAL_API_KEY` is set. In scope:

- **API key handling.** `DECIMAL_API_KEY` is optional, but when it is set it lives in the client's
  config. Any path that writes it to stdout, into a tool result, into a log or crash message, or to
  any host other than the configured `DECIMAL_API_URL` is a vulnerability.
- **Anything beyond read.** The three tools are read-only by design. A code path that writes to the
  filesystem, spawns a process, opens a network connection to somewhere other than the configured
  API, or mutates registry state is in scope even if you cannot yet show harm.
- **Untrusted registry content reaching the model.** `get_skill` returns a `SKILL.md` body written by
  a third party, and that text lands in the client's context. Failing to keep it bounded and clearly
  delimited as untrusted data — so that it can pose as tool output, as server instructions, or as the
  client's own framing — is in scope. Prompt injection is the realistic threat model for an MCP
  server that serves other people's text, and we would rather hear about it than not.
- **Protocol handling.** Malformed or hostile MCP messages, or malformed API responses, that cause
  the server to crash, hang, or emit something outside its schema.
- **The published artifacts** — the `decimalai-mcp` wheel and sdist on PyPI, the `uvx decimalai-mcp`
  path, and the registry listings in `server.json` / `server.io-github.json` / `smithery.yaml` /
  `glama.json`. A published package that does not match this source tree, a listing that points at
  the wrong artifact, or a typosquat impersonating either name is in scope. Report those here even
  though the fix is not a code change.

**Out of scope**

- **Malicious or misleading skill content on the registry itself.** Email us anyway — that is an
  abuse report and we act on it — but it is a registry content problem, not a defect in this server.
- The DecimalAI hosted API (`api.decimal.ai`) and the registry web app. Report those the same way, to
  the same address; they are just fixed elsewhere.
- Vulnerabilities in the MCP client (Claude Desktop, Cursor, and so on), in the MCP SDK, or in `uv` /
  `uvx`. Report those to their maintainers, and tell us if our use is what makes them reachable.
- The fact that setting `DECIMAL_API_URL` points the server at an arbitrary host. That is a
  deliberate feature for self-hosted and local backends; the user configures it.
- Dependency CVEs with no reachable path through this server.
- Scanner output with no demonstrated impact.

## What happens next

We are a small team, so rather than publish a response time we cannot hold to, here is what we
actually do:

- We acknowledge a report once we have read it, and we say plainly if triage is going to take a
  while.
- We tell you whether we consider it in scope and what we intend to do.
- We follow coordinated disclosure. We agree a timeline with you rather than impose one, and we will
  not ask you to stay quiet indefinitely. A fix here means a PyPI release plus updated registry
  listings, and we will tell you when both have landed.
- We are happy to credit you in the advisory and the release notes. Tell us how you would like to be
  named, or say that you would rather not be.

There is no paid bug bounty. That is a resourcing decision, not a judgment about the value of your
work.

## Safe harbour

If you make a good-faith effort to follow this policy, we will not pursue or support legal action
against you for your research. Good faith means avoiding privacy violations and service degradation,
only interacting with accounts and data you own or have permission to test, and giving us a
reasonable opportunity to fix the issue before you disclose it publicly.

If you are not sure whether what you found is a security issue, email
[hello@decimal.ai](mailto:hello@decimal.ai) and ask. That is always the right call.
