"""HTTP layer + formatting for the DecimalAI MCP server.

Kept separate from ``server.py`` so the logic is testable without the
``mcp`` package: every tool in ``server.py`` is a thin wrapper around a
function in this module.

All three tools read PUBLIC endpoints of the DecimalAI registry
(verified against the platform backend, 2026-07-14):

  GET /api/v1/registry/skills          — browse/search (anonymous OK)
  GET /api/v1/registry/skills/{slug}   — detail incl. trust + benchmarks (anonymous OK)
  GET /api/v1/registry/leaderboard     — ranked leaderboard (anonymous OK)

No API key is required. If ``DECIMAL_API_KEY`` is set, requests carry
``Authorization: Bearer …`` and the same endpoints enrich results with
per-user fields (e.g. ``installed_as`` — whether your org already forked
the skill).

Deliberately ABSENT: ``check_manifest_impact``. The impact endpoint
(``POST /api/v1/regression-check``) requires authentication on the
platform — there is no public variant — and this server is read-only
public-registry surface. See README "Why no manifest-impact tool?".
"""

from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "https://api.decimal.ai"

# Test seam: tests assign an ``httpx.MockTransport`` here so no real
# network traffic happens. ``None`` → httpx default transport.
_transport: Optional[httpx.BaseTransport] = None

_LEADERBOARD_SORTS = {"skill_score", "biggest_improvement", "efficiency", "top_rated"}


def _base_url() -> str:
    return os.environ.get("DECIMAL_API_URL", DEFAULT_BASE_URL).rstrip("/")


def _headers() -> dict[str, str]:
    # Keep in step with __version__ / pyproject.toml on every release —
    # see the version table in RELEASING.md.
    headers = {"User-Agent": "decimalai-mcp/0.1.1"}
    api_key = os.environ.get("DECIMAL_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_base_url(),
        headers=_headers(),
        timeout=15.0,
        transport=_transport,
    )


def _get(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    with _client() as client:
        resp = client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()


# ── Formatting helpers ─────────────────────────────────────────


def _fmt_score(value: Any) -> str:
    """SkillScore is 0..1; None means 'New' (no evidence yet)."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value: Any, signed: bool = False) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{v:+.1f}" if signed else f"{v:.1f}"


def _skill_line(item: dict[str, Any], rank: Optional[int] = None) -> str:
    name = item.get("name", "?")
    eff = item.get("effectiveness") or {}
    parts = [
        f"{rank}. **{name}**" if rank is not None else f"- **{name}**",
    ]
    desc = (item.get("description") or "").strip()
    if desc:
        parts.append(desc if len(desc) <= 140 else desc[:137] + "...")
    meta = []
    metric = item.get("metric") or {}
    if metric.get("label"):
        # Leaderboard rows carry a headline metric ("+12 pts pass rate", …)
        meta.append(metric["label"])
    score = eff.get("skill_score")
    if score is not None:
        meta.append(f"SkillScore {_fmt_score(score)}")
    bench = item.get("benchmark_summary") or {}
    if bench.get("pass_rate_delta_pts") is not None:
        meta.append(f"benchmark lift {_fmt_pct(bench['pass_rate_delta_pts'], signed=True)} pts")
    if item.get("skill_safety"):
        meta.append(f"safety: {item['skill_safety']}")
    if item.get("skill_badge"):
        meta.append(f"badge: {item['skill_badge']}")
    installs = item.get("install_count")
    if installs:
        meta.append(f"{installs} installs")
    if item.get("installed_as"):
        meta.append(f"already installed as `{item['installed_as']}`")
    if meta:
        parts.append("(" + ", ".join(meta) + ")")
    return " — ".join(parts[:2]) + (" " + parts[2] if len(parts) > 2 else "")


# ── Tool implementations ───────────────────────────────────────


def search_skills(
    query: str,
    category: Optional[str] = None,
    sort: str = "recommended",
    limit: int = 10,
) -> str:
    """Search the public DecimalAI skills registry."""
    params: dict[str, Any] = {
        "q": query,
        "sort": sort,
        "limit": max(1, min(int(limit), 50)),
    }
    if category:
        params["category"] = category
    data = _get("/api/v1/registry/skills", params=params)
    items = data.get("items", [])
    if not items:
        return f"No public skills matched {query!r}."
    total = data.get("total_hint") or data.get("total") or len(items)
    lines = [f"## Registry results for {query!r} ({len(items)} of {total})", ""]
    lines += [_skill_line(item) for item in items]
    lines += ["", "Use `get_skill(<name>)` for trust/scan status, benchmark evidence, and the skill body."]
    return "\n".join(lines)


def get_skill(slug: str) -> str:
    """Full detail for one registry skill: trust, safety scan, benchmarks, body."""
    try:
        # quote() the model-supplied slug: unencoded '/'/'?' would let a
        # prompt-injected value reach arbitrary same-host paths (with the
        # user's bearer header when DECIMAL_API_KEY is set).
        skill = _get(f"/api/v1/registry/skills/{quote(slug, safe='')}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return (
                f"No public registry skill named {slug!r}. "
                "Try search_skills() first — the slug is the `name` field."
            )
        raise

    eff = skill.get("effectiveness") or {}
    bench = skill.get("benchmark_summary") or {}
    safety = skill.get("safety") or {}

    lines = [f"# {skill.get('display_name') or skill.get('name', slug)}"]
    desc = (skill.get("description") or "").strip()
    if desc:
        lines += ["", desc]

    lines += ["", "## Trust & safety"]
    lines.append(f"- Unified safety band: **{skill.get('skill_safety', 'unreviewed')}** "
                 "(worst-of: scanner / intent review / content review)")
    lines.append(f"- Scanner: {skill.get('safety_status', 'unscanned')}"
                 + (f" — {safety.get('summary')}" if safety.get("summary") else ""))
    lines.append(f"- Intent review: {skill.get('intent_status', 'unreviewed')}")
    lines.append(f"- Content review: {skill.get('content_status', 'unreviewed')}")
    if skill.get("skill_badge"):
        lines.append(f"- Badge: {skill['skill_badge']}")

    lines += ["", "## Evidence"]
    lines.append(f"- SkillScore (v2, evidence-tiered): {_fmt_score(eff.get('skill_score'))}")
    if eff.get("avg_pass_rate") is not None:
        lines.append(f"- Live pass rate: {_fmt_pct(100 * float(eff['avg_pass_rate']))}%")
    if bench:
        lines.append(
            f"- Latest verified benchmark: verdict **{bench.get('verdict', '?')}**, "
            f"lift {_fmt_pct(bench.get('pass_rate_delta_pts'), signed=True)} pts "
            f"({bench.get('passed_cases', '?')}/{bench.get('total_cases', '?')} cases)"
        )
        if bench.get("never_hurt") is not None:
            lines.append(f"- Never-hurt (zero regressed cases): {bench['never_hurt']}")
    history = skill.get("verification_history") or []
    if history:
        latest = history[0]
        lines.append(
            f"- Last re-verified: {latest.get('verified_at', '?')} "
            f"({latest.get('verification_method', 'verified')} on {latest.get('runner_model', '?')})"
        )
    lines.append(f"- Installs: {skill.get('install_count', 0)}; "
                 f"rating: {skill.get('avg_rating') or '—'} ({skill.get('rating_count', 0)} ratings)")
    if skill.get("installed_as"):
        lines.append(f"- Already installed in your org as `{skill['installed_as']}`")

    body = (skill.get("body_markdown") or "").strip()
    if body:
        truncated = body if len(body) <= 4000 else body[:4000] + "\n\n… [truncated]"
        lines += ["", "## SKILL.md body", "", truncated]

    lines += ["", f"Web page: https://app.decimal.ai/skills/{skill.get('name', slug)}"]
    return "\n".join(lines)


def get_leaderboard(
    sort: str = "biggest_improvement",
    category: Optional[str] = None,
    window_days: int = 30,
    limit: int = 10,
) -> str:
    """Ranked skills leaderboard.

    Without ``category``: the dedicated public leaderboard endpoint
    (axes: skill_score | biggest_improvement | efficiency | top_rated).
    With ``category``: the leaderboard endpoint does not support category
    filtering server-side, so we fall back to the registry browse endpoint
    in its documented ranked view (``view=ranks``) filtered by category.
    """
    limit = max(1, min(int(limit), 50))
    if category:
        data = _get(
            "/api/v1/registry/skills",
            params={"category": category, "sort": sort, "view": "ranks", "limit": limit},
        )
        items = data.get("items", [])
        title = f"## Top skills in category {category!r} (sorted by {sort})"
    else:
        if sort not in _LEADERBOARD_SORTS:
            return (
                f"Unsupported sort {sort!r}. The public leaderboard supports: "
                + ", ".join(sorted(_LEADERBOARD_SORTS))
            )
        data = _get(
            "/api/v1/registry/leaderboard",
            params={"sort": sort, "window_days": window_days, "limit": limit},
        )
        items = data.get("items", [])
        title = f"## Skills leaderboard — {sort} (last {window_days} days)"

    if not items:
        return "The leaderboard returned no entries for these filters."
    lines = [title, ""]
    lines += [_skill_line(item, rank=i + 1) for i, item in enumerate(items)]
    # Deliberately no leg/activation thresholds here. They have moved twice already
    # (the ≥2-leg gate became ≥1 in June, a ≥10-activation gate was dropped in August),
    # and a number hardcoded in a shipped PyPI package cannot be corrected without a
    # release — meanwhile every MCP client reads this line into the model's context as
    # fact. Name the axis, link the rules.
    lines += ["", "Every axis is an earned place. The per-axis gates are documented at "
              "https://docs.decimal.ai/guides/skillscore."]
    return "\n".join(lines)
