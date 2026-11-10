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
import shlex
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
    headers = {"User-Agent": "decimalai-mcp/0.1.5"}
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
    """SkillScore out of 100, as the skill's page shows it. The registry sends 0..1; None means no
    evidence yet."""
    if value is None:
        return "—"
    try:
        return f"{round(max(0.0, min(1.0, float(value))) * 100)}/100"
    except (TypeError, ValueError):
        return str(value)


# Until this date a benchmark stored a case as failed when no check separated the two runs, even when
# every check passed, and those counts were never repaired: `browser-automation` stored 2 of 23 where
# its own checks say 23 of 23. The skill's page corrects them from the run's checks, which these
# endpoints don't carry, so no count is shown for such a run. The same rule and sentence as the
# `decimalai skills search` CLI (decimalai 0.28.1, `_COUNTS_FROM_CHECKS_SINCE`).
_COUNTS_FROM_CHECKS_SINCE = "2026-08-01"
PRE_AUGUST_NOTE = "Tested before August 2026, when results were counted differently, so no count is shown."


def _test_result(bench: Any) -> Optional[str]:
    """The sentence a skill's page leads with, from the registry's own summary, or None."""
    if not isinstance(bench, dict) or bench.get("lift_withheld_reason"):
        return None
    cases, rate, lift = (bench.get(k) for k in ("lift_cases", "lift_pass_rate_pct", "pass_rate_delta_pts"))
    if not (isinstance(cases, int) and cases > 0 and isinstance(rate, (int, float))
            and isinstance(lift, (int, float))):
        return None
    completed = bench.get("completed_at")
    if isinstance(completed, str) and completed < _COUNTS_FROM_CHECKS_SINCE:
        return PRE_AUGUST_NOTE
    # Rounds as the page does (half up), so the two never disagree on a count.
    with_skill = int(rate / 100 * cases + 0.5)
    without = int((rate - lift) / 100 * cases + 0.5)
    return (f"Passed {with_skill} of {cases} test cases with the skill, and {without} of {cases} "
            f"without it ({_fmt_pct(lift, signed=True)} pts).")


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
    # The identifier get_skill() actually takes. A registry `name` may be
    # namespaced (`owner/skill`) and the detail endpoint is a single path
    # segment, so the namespaced form 404s there — `url_slug` is the slash-free
    # identifier minted for it. Surface it whenever it differs from the name,
    # otherwise the model has no way to reach the record it just found. Omitted
    # when the two are identical (the plain-name case) so the common row stays short.
    slug = item.get("url_slug")
    if slug and slug != name:
        meta.append(f"get_skill slug: `{slug}`")
    metric = item.get("metric") or {}
    if metric.get("label"):
        # Leaderboard rows carry a headline metric ("+12 pts pass rate", …)
        meta.append(metric["label"])
    score = eff.get("skill_score")
    if score is not None:
        meta.append(f"SkillScore {_fmt_score(score)}")
    bench = item.get("benchmark_summary") or {}
    completed = bench.get("completed_at")
    if isinstance(completed, str) and completed < _COUNTS_FROM_CHECKS_SINCE:
        meta.append("tested before August 2026, no count shown")
    elif bench.get("pass_rate_delta_pts") is not None:
        meta.append(f"benchmark lift {_fmt_pct(bench['pass_rate_delta_pts'], signed=True)} pts")
    if item.get("skill_safety"):
        meta.append(f"safety: {item['skill_safety']}")
    if item.get("skill_badge"):
        meta.append(f"badge: {item['skill_badge']}")
    downloads = item.get("download_count")
    if downloads:
        meta.append(f"{downloads} downloads")
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
    total = data.get("total_hint") or data.get("total")
    if total:
        shown = f"{len(items)} of {total}"
    elif data.get("has_next"):
        shown = f"first {len(items)}; more match"
    else:
        shown = f"{len(items)}"
    lines = [f"## Registry results for {query!r} ({shown})", ""]
    lines += [_skill_line(item) for item in items]
    lines += ["", "Use `get_skill(<url_slug>)` for trust/scan status, benchmark evidence, and the "
              "skill body — the slug shown above where it differs from the name, otherwise the name itself."]
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
                "Try search_skills() first and pass the `url_slug` field, not `name` — "
                "a namespaced name (`owner/skill`) cannot be a single URL path segment, "
                "so it always 404s here; the slug is its slash-free form (`owner-skill`)."
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
    lines.append(f"- SkillScore: {_fmt_score(eff.get('skill_score'))}")
    if eff.get("avg_pass_rate") is not None:
        lines.append(f"- Live pass rate: {_fmt_pct(100 * float(eff['avg_pass_rate']))}%")
    if bench:
        result = _test_result(bench)
        if result == PRE_AUGUST_NOTE:
            lines.append(f"- Latest verified benchmark: {result}")
        else:
            lines.append(f"- Latest verified benchmark: verdict **{bench.get('verdict', '?')}**. "
                         + (result or f"Lift {_fmt_pct(bench.get('pass_rate_delta_pts'), signed=True)} pts."))
            if bench.get("never_hurt") is not None:
                lines.append(f"- Never-hurt (zero regressed cases): {bench['never_hurt']}")
        measured = _measured_version(skill)
        if measured:
            lines.append(f"- {measured}")
    history = skill.get("verification_history") or []
    if history:
        latest = history[0]
        lines.append(
            f"- Last re-verified: {latest.get('verified_at', '?')} "
            f"({latest.get('verification_method', 'verified')} on {latest.get('runner_model', '?')})"
        )
    lines.append(f"- {_usage_line(skill)}; "
                 f"rating: {skill.get('avg_rating') or '—'} ({skill.get('rating_count', 0)} ratings)")
    if skill.get("installed_as"):
        lines.append(f"- Already installed in your org as `{skill['installed_as']}`")

    body = (skill.get("body_markdown") or "").strip()
    lines += _install_lines(skill, slug, has_body=bool(body))

    if body:
        truncated = body if len(body) <= 4000 else body[:4000] + "\n\n… [truncated]"
        lines += ["", "## SKILL.md body", "", truncated]

    # /skills/<url_slug>, not /skills/<name>: the web route is one path segment too,
    # so a namespaced name renders an extra segment and 404s (verified live 2026-08-15,
    # /skills/wshobson/python-error-handling -> 404 vs the slug -> 200). Fall back to
    # `name` for older backends that do not send url_slug, where the two are identical.
    lines += ["", "Web page: https://app.decimal.ai/skills/"
              f"{skill.get('url_slug') or skill.get('name') or slug}"]
    return "\n".join(lines)


def _install_lines(skill: dict[str, Any], slug: str, has_body: bool) -> list[str]:
    """The folder a skill installs into, and the command that installs it.

    Two identifiers, deliberately. The command's ref is ``url_slug`` (the name for
    a registry that sends none), which ``decimalai skills add`` resolves. The
    folder is ``install_name``: the folder name and the ``name:`` its served
    SKILL.md declares. It is not the url_slug, which can keep ``_`` or ``.``, and
    loaders refuse those in ``name:``.

    Empty in two cases:

    * The skill is blocked, withheld from listings, or its safety scan is flagged
      and awaiting review (which the registry refuses to install publicly even
      though its band is only "caution"). The registry still serves the record,
      install name included, so the verdict is readable; nothing here may then
      recommend installing it. A missing ``safety_status`` reads as not flagged,
      as the registry's own default for it is "unscanned".
    * The registry sends no ``install_name``. The folder name is the server's to
      choose, by rules a copy in this package could drift from with no way to
      correct it short of a release, and the rest of the section describes that
      folder, so it is left out whole. An older registry gets exactly the output
      it always got.

    Receiving ``install_name`` is not taken to mean the registry serves the
    routes ``skills add`` calls: a registry can send the field before it serves
    those routes, and nothing here checks for them.
    """
    if (
        skill.get("skill_safety") == "blocked"
        or skill.get("safety_status") == "flagged"
        or skill.get("withheld")
        or skill.get("withheld_reason")
    ):
        return []
    install_name = skill.get("install_name")
    if not install_name:
        return []
    ref = skill.get("url_slug") or skill.get("name") or slug
    folder = (f"- Install name: `{install_name}` (the folder it installs into, "
              "and the `name:` its SKILL.md declares")
    alt = skill.get("install_name_disambiguated")
    if alt and alt != install_name:
        # The installer picks this form when the project already holds a
        # different skill (or an unmanaged folder) under the plain name.
        folder += f"; `{alt}` if that folder is already taken in the project"
    command = (f"- `decimalai skills add {shlex.quote(ref)}` "
               '(decimalai 0.15 or later: `pip install "decimalai>=0.15"`) '
               "writes that folder with the registry's SKILL.md and pins the version.")
    if has_body:
        command += (" Use it instead of saving the body below as SKILL.md: the body "
                    "has no frontmatter, and may be cut short.")
    # The command writes a plugin folder, which interactive Claude Code reads only
    # when started with --plugin-dir. The only place this output names that folder.
    claude_code = ("- In Claude Code, load the project's installed skills with "
                   '`claude --plugin-dir "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.agents"` '
                   "(run it from the project's folder).")
    return ["", "## Install", folder + ")", command, claude_code]


def _measured_version(skill: dict[str, Any]) -> Optional[str]:
    """Which version the headline benchmark measured, when it is not the latest one."""
    latest = skill.get("latest_version_number")
    headline = next((m for m in (skill.get("benchmark_models") or [])
                     if isinstance(m, dict) and m.get("headline")), None)
    measured = (headline or {}).get("version_number")
    if isinstance(latest, int) and isinstance(measured, int) and measured != latest:
        return f"This benchmark measured v{measured}; the latest version is v{latest}."
    return None


def _usage_line(skill: dict[str, Any]) -> str:
    """The skill page's track record, in its words: downloads, agent runs, forks."""
    scope = skill.get("usage_scope") or {}
    runs = (scope.get("total_uses") if scope.get("total_uses") is not None
            else skill.get("cumulative_recorded_uses") or scope.get("cumulative_recorded_uses")
            or skill.get("total_uses") or 0)
    parts = []
    downloads = skill.get("download_count") or 0
    if downloads:
        since = skill.get("downloads_since")
        parts.append(f"Downloads: {downloads}" + (f" (since {since})" if since else ""))
    parts.append(f"Agent runs: {runs}")
    forks = skill.get("fork_count") or 0
    if forks:
        parts.append(f"Forks: {forks}")
    return "; ".join(parts)


def get_leaderboard(
    sort: str = "biggest_improvement",
    category: Optional[str] = None,
    window_days: int = 30,
    limit: int = 10,
) -> str:
    """Ranked skills leaderboard, from the public leaderboard endpoint
    (axes: skill_score | biggest_improvement | efficiency | top_rated).

    ``category`` narrows every axis to one category and re-ranks inside it; the
    endpoint does that itself. Until 2026-11-10 a category went to the registry
    browse endpoint instead, with the leaderboard's sort name, which browse
    doesn't take: `category=data, sort=skill_score` was a raw 400.
    """
    limit = max(1, min(int(limit), 50))
    if sort not in _LEADERBOARD_SORTS:
        return (
            f"Unsupported sort {sort!r}. The public leaderboard supports: "
            + ", ".join(sorted(_LEADERBOARD_SORTS))
        )
    params = {"sort": sort, "window_days": window_days, "limit": limit}
    if category:
        params["category"] = category
    data = _get("/api/v1/registry/leaderboard", params=params)
    items = data.get("items", [])
    scope = (data.get("usage_scope") or {}).get("quality_scope")
    title = (f"## Skills leaderboard — {sort}" + (f" in {category!r}" if category else "")
             + (f" ({scope})" if scope else ""))

    if not items:
        if category:
            return (f"No ranked skills in category {category!r}. Category names are the ones the "
                    "registry's filters show, in lower case, e.g. 'documentation' or 'code-review': "
                    "https://app.decimal.ai/skills")
        return "The leaderboard returned no entries for these filters."
    lines = [title, ""]
    lines += [_skill_line(item, rank=i + 1) for i, item in enumerate(items)]
    # Deliberately no leg/activation thresholds here. They have moved twice already
    # (the ≥2-leg gate became ≥1 in June, a ≥10-activation gate was dropped in August),
    # and a number hardcoded in a shipped PyPI package cannot be corrected without a
    # release — meanwhile every MCP client reads this line into the model's context as
    # fact. Name the axis, link the rules.
    lines += ["", "Every axis is an earned place. How the axes rank and what evidence "
              "they need: https://docs.decimal.ai/guides/registry#how-effectiveness-is-computed."]
    return "\n".join(lines)
