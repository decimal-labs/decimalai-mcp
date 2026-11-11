"""Unit tests for the DecimalAI MCP server's tool logic.

All HTTP is mocked with ``httpx.MockTransport`` — no network. The response
shapes mirror the registry API's own documented response examples for the
browse, detail, and leaderboard endpoints (see the OpenAPI schema at
https://docs.decimal.ai).
"""

from __future__ import annotations

import json

import httpx
import pytest

from decimalai_mcp import api


BROWSE_RESPONSE = {
    "items": [
        {
            "id": "sk_pub123",
            "name": "semantic-search",
            "description": "Semantic search over knowledge bases",
            "category": "retrieval",
            "skill_badge": "verified",
            "skill_safety": "passed",
            "install_count": 230,
            "effectiveness": {"skill_score": 0.85},
            "benchmark_summary": {"verdict": "pass", "pass_rate_delta_pts": 12.0},
        },
    ],
    # Live browse payload keys (verified 2026-07-23): items / next_cursor /
    # has_next / total_hint — there is no top-level "total".
    "total_hint": 45,
    "has_next": True,
    "next_cursor": None,
}

DETAIL_RESPONSE = {
    "id": "sk_pub123",
    "name": "semantic-search",
    "display_name": "Semantic Search",
    "description": "Semantic search over knowledge bases",
    "category": "retrieval",
    "skill_badge": "verified",
    "skill_safety": "passed",
    "safety_status": "clean",
    "safety": {"status": "clean", "summary": "No findings"},
    "intent_status": "approved",
    "content_status": "approved",
    "install_count": 230,
    "avg_rating": 4.5,
    "rating_count": 18,
    "effectiveness": {"skill_score": 0.85, "avg_pass_rate": 0.92},
    "benchmark_summary": {
        "verdict": "pass",
        "total_cases": 24,
        "passed_cases": 22,
        "pass_rate_delta_pts": 12.0,
        "never_hurt": True,
    },
    "verification_history": [
        {
            "runner_model": "gemini-3.5-flash",
            "verified_at": "2026-07-01T00:00:00Z",
            "verification_method": "replay",
            "delta_pts": 12.0,
        }
    ],
    "body_markdown": "## When to use\n\nUse this skill for KB questions.",
}

# The install fields a registry adds to the detail record: the folder the skill
# installs into, in its two forms, beside the url_slug that `decimalai skills add`
# takes as its ref. DETAIL_RESPONSE above has none of these keys, which is what
# an older registry sends.
INSTALL_FIELDS = {
    "url_slug": "semantic-search",
    "install_name": "semantic-search",
    "install_name_disambiguated": "semantic-search-5d41a2",
}

LEADERBOARD_RESPONSE = {
    "sort": "biggest_improvement",
    "window_days": 30,
    "items": [
        {
            "rank": 1,
            "name": "sql-analyst",
            "description": "Exec-graded SQL answering",
            "skill_badge": "verified",
            "install_count": 90,
            "metric": {"label": "+66 pts pass rate", "value": 66.0, "kind": "pass_rate_improvement"},
        },
        {
            "rank": 2,
            "name": "semantic-search",
            "description": "Semantic search over knowledge bases",
            "install_count": 230,
            "metric": {"label": "+12 pts pass rate", "value": 12.0, "kind": "pass_rate_improvement"},
        },
    ],
    "total": 2,
}


def _boom() -> None:
    """Stand-in for `mcp.run` — fails loudly if a flag path ever serves."""
    raise AssertionError("mcp.run() must not be called for a flag invocation")


@pytest.fixture
def capture(monkeypatch):
    """Install a MockTransport; captured[0] is the last request seen."""
    captured: list[httpx.Request] = []

    def make_handler(payload, status_code=200):
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(status_code, json=payload)
        return handler

    def install(payload, status_code=200):
        monkeypatch.setattr(
            api, "_transport", httpx.MockTransport(make_handler(payload, status_code))
        )
        return captured

    return install


class TestSearchSkills:
    def test_hits_public_registry_endpoint_with_query(self, capture):
        captured = capture(BROWSE_RESPONSE)
        out = api.search_skills("semantic search")
        req = captured[0]
        assert req.url.path == "/api/v1/registry/skills"
        assert req.url.params["q"] == "semantic search"
        assert req.url.params["sort"] == "recommended"
        assert "semantic-search" in out
        assert "(1 of 45)" in out  # total_hint, not the absent "total" key
        assert "SkillScore 85/100" in out      # the page's scale, not 0..1
        assert "+12.0 pts" in out  # benchmark lift surfaced

    def test_no_auth_header_without_api_key(self, capture, monkeypatch):
        monkeypatch.delenv("DECIMAL_API_KEY", raising=False)
        captured = capture(BROWSE_RESPONSE)
        api.search_skills("x")
        assert "authorization" not in captured[0].headers

    def test_api_key_env_unlocks_bearer_auth(self, capture, monkeypatch):
        monkeypatch.setenv("DECIMAL_API_KEY", "dai_sk_test")
        captured = capture(BROWSE_RESPONSE)
        api.search_skills("x")
        assert captured[0].headers["authorization"] == "Bearer dai_sk_test"

    def test_base_url_override(self, capture, monkeypatch):
        monkeypatch.setenv("DECIMAL_API_URL", "http://localhost:8000")
        captured = capture(BROWSE_RESPONSE)
        api.search_skills("x")
        assert captured[0].url.host == "localhost"

    def test_category_filter_and_limit_clamp(self, capture):
        captured = capture(BROWSE_RESPONSE)
        api.search_skills("x", category="retrieval", limit=500)
        params = captured[0].url.params
        assert params["category"] == "retrieval"
        assert params["limit"] == "50"

    def test_empty_results(self, capture):
        capture({"items": [], "total": 0})
        out = api.search_skills("no-such-thing")
        assert "No public skills matched" in out

    def test_namespaced_result_surfaces_the_slug_get_skill_takes(self, capture):
        """A namespaced `name` is NOT a usable get_skill argument.

        The detail endpoint is a single URL path segment, so `owner/skill` 404s
        there (verified live 2026-08-15). `url_slug` is the slash-free identifier
        minted for it — the row has to carry it or the model cannot reach the
        record it just found.
        """
        capture(dict(BROWSE_RESPONSE, items=[
            dict(BROWSE_RESPONSE["items"][0],
                 name="owner/semantic-search", url_slug="owner-semantic-search"),
        ]))
        out = api.search_skills("x")
        assert "**owner/semantic-search**" in out       # identity still shown
        assert "get_skill slug: `owner-semantic-search`" in out
        assert "get_skill(<url_slug>)" in out           # footer points at the slug

    def test_plain_name_row_stays_short(self, capture):
        """When url_slug == name there is nothing to disambiguate — no extra field."""
        capture(dict(BROWSE_RESPONSE, items=[
            dict(BROWSE_RESPONSE["items"][0], url_slug="semantic-search"),
        ]))
        out = api.search_skills("x")
        assert "get_skill slug:" not in out


class TestGetSkill:
    def test_detail_includes_trust_and_benchmark(self, capture):
        captured = capture(DETAIL_RESPONSE)
        out = api.get_skill("semantic-search")
        assert captured[0].url.path == "/api/v1/registry/skills/semantic-search"
        assert "**passed**" in out            # unified safety band
        assert "Scanner: clean" in out
        assert "Intent review: approved" in out
        assert "verdict **pass**" in out      # benchmark verdict
        assert "+12.0 pts" in out
        assert "Never-hurt" in out
        assert "replay" in out                # verification history method
        assert "## SKILL.md body" in out
        assert "app.decimal.ai/skills/semantic-search" in out

    def test_slug_is_percent_encoded_in_path(self, capture):
        captured = capture(DETAIL_RESPONSE)
        api.get_skill("../../api-keys?x=1")
        raw = captured[0].url.raw_path.decode()
        assert raw == "/api/v1/registry/skills/..%2F..%2Fapi-keys%3Fx%3D1"

    def test_404_returns_helpful_message(self, capture):
        capture({"detail": "Registry skill not found"}, status_code=404)
        out = api.get_skill("nope")
        assert "No public registry skill named 'nope'" in out
        # The recovery hint must name url_slug. It used to say "the slug is the
        # `name` field", which is the exact advice that produces this 404 for any
        # namespaced skill.
        assert "url_slug" in out
        assert "the slug is the `name` field" not in out

    def test_web_page_link_uses_url_slug_not_the_namespaced_name(self, capture):
        """The detail footer must link /skills/<url_slug>.

        A registry name may be namespaced (`owner/skill`). Interpolated into the
        web route it renders an extra path segment and 404s — verified live
        2026-08-15: /skills/wshobson/python-error-handling -> 404, while
        /skills/wshobson-python-error-handling -> 200.
        """
        capture(dict(DETAIL_RESPONSE, name="owner/semantic-search",
                     url_slug="owner-semantic-search"))
        out = api.get_skill("owner-semantic-search")
        assert "app.decimal.ai/skills/owner-semantic-search" in out
        assert "app.decimal.ai/skills/owner/semantic-search" not in out

    def test_web_page_link_falls_back_to_name_without_url_slug(self, capture):
        """Older backends do not send url_slug; behaviour there is unchanged."""
        capture(DETAIL_RESPONSE)  # no url_slug key
        out = api.get_skill("semantic-search")
        assert "app.decimal.ai/skills/semantic-search" in out

    def test_long_body_truncated(self, capture):
        detail = dict(DETAIL_RESPONSE, body_markdown="x" * 10000)
        capture(detail)
        out = api.get_skill("semantic-search")
        assert "[truncated]" in out
        assert len(out) < 9000


class TestGetSkillInstall:
    """get_skill names the folder a skill installs into and the command that installs it."""

    def test_names_the_install_folder_and_the_skills_add_command(self, capture):
        capture(dict(DETAIL_RESPONSE, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert "## Install" in out
        assert "Install name: `semantic-search`" in out
        assert "`decimalai skills add semantic-search`" in out
        # The command does not exist before 0.15, so the line says which release has it.
        assert 'pip install "decimalai>=0.15"' in out

    def test_names_the_fallback_folder_for_a_taken_name(self, capture):
        """The project can already hold a different skill in that folder.

        The installer then writes the hashed form instead, so "the folder" is only
        accurate if the fallback is named beside it.
        """
        capture(dict(DETAIL_RESPONSE, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert "`semantic-search-5d41a2`" in out

    @pytest.mark.parametrize("alt", [None, "", "semantic-search"],
                             ids=["absent", "empty", "same-as-install-name"])
    def test_no_fallback_clause_when_there_is_no_distinct_fallback(self, capture, alt):
        capture(dict(DETAIL_RESPONSE, **dict(INSTALL_FIELDS, install_name_disambiguated=alt)))
        out = api.get_skill("semantic-search")
        assert "Install name: `semantic-search`" in out
        assert "already taken" not in out

    def test_tells_claude_code_users_how_to_load_the_installed_skills(self, capture):
        """`skills add` writes a plugin folder that interactive Claude Code reads only
        when started with --plugin-dir, so the section says how. One line, and the
        only place the output names that folder."""
        capture(dict(DETAIL_RESPONSE, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        install = out[out.index("## Install"):out.index("## SKILL.md body")]
        line = ('- In Claude Code, load the project\'s installed skills with '
                '`claude --plugin-dir "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.agents"` '
                "(run it from the project's folder).")
        assert line in install.splitlines()
        assert out.count(".agents") == 1

    def test_command_takes_the_url_slug_and_the_folder_takes_the_install_name(self, capture):
        """Two identifiers, not one.

        `url_slug` is the ref the command resolves; it can keep `_` or `.`. The
        install name is the folder, and loaders refuse those characters in it. A
        namespaced name is neither: it carries a slash.
        """
        capture(dict(DETAIL_RESPONSE, name="acme/pdf_tools", url_slug="acme-pdf_tools",
                     install_name="acme-pdf-tools",
                     install_name_disambiguated="acme-pdf-tools-3f9a1c"))
        out = api.get_skill("acme-pdf_tools")
        assert "`decimalai skills add acme-pdf_tools`" in out
        assert "Install name: `acme-pdf-tools`" in out
        assert "skills add acme/pdf_tools" not in out
        assert "skills add acme-pdf-tools`" not in out

    def test_command_falls_back_to_the_name_without_url_slug(self, capture):
        capture(dict(DETAIL_RESPONSE, install_name="semantic-search"))  # no url_slug key
        out = api.get_skill("semantic-search")
        assert "`decimalai skills add semantic-search`" in out

    def test_ref_is_shell_quoted(self, capture):
        """A model may paste this line into a shell, so the ref must stay one argument."""
        capture(dict(DETAIL_RESPONSE, url_slug="x; touch pwned", install_name="x-touch-pwned"))
        out = api.get_skill("x")
        assert "`decimalai skills add 'x; touch pwned'`" in out

    @pytest.mark.parametrize("fields", [{}, {"install_name": None}, {"install_name": ""}],
                             ids=["key-absent", "null", "empty"])
    def test_no_install_section_without_an_install_name(self, capture, fields):
        """No install name, no section: the name is not guessed.

        The folder name is the server's to choose, and a copy of its rules in this
        package could not be corrected without a release. The rest of the section
        describes that folder, so it is left out whole rather than half-shown.
        """
        capture(dict(DETAIL_RESPONSE, url_slug="semantic-search", **fields))
        out = api.get_skill("semantic-search")
        assert "## Install" not in out
        assert "skills add" not in out
        assert "--plugin-dir" not in out
        # The rest of the record is untouched.
        assert "## SKILL.md body" in out
        assert "app.decimal.ai/skills/semantic-search" in out

    def test_no_install_section_for_a_blocked_skill(self, capture):
        """The registry serves a blocked skill's record, install name included, so its
        verdict is readable. Reading it is not a reason to install it."""
        capture(dict(DETAIL_RESPONSE, skill_safety="blocked", safety_status="blocked",
                     **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert "**blocked**" in out          # the verdict is still shown
        assert "## Install" not in out
        assert "Install name" not in out
        assert "skills add" not in out
        assert "--plugin-dir" not in out

    @pytest.mark.parametrize("fields", [
        {"withheld": "safety_blocked", "withheld_reason": "safety_blocked"},
        {"withheld": "intent_rejected", "withheld_reason": "intent_rejected"},
        # Flagged content is withheld while its band is only "caution".
        {"withheld": "content_blocked", "withheld_reason": "content_blocked",
         "skill_safety": "caution", "content_status": "flagged"},
        {"withheld_reason": "content_blocked"},
    ], ids=["safety", "intent", "content-flagged", "reason-key-only"])
    def test_no_install_section_for_a_withheld_skill(self, capture, fields):
        capture(dict(DETAIL_RESPONSE, **dict(INSTALL_FIELDS, **fields)))
        out = api.get_skill("semantic-search")
        assert "## Install" not in out
        assert "skills add" not in out
        assert "--plugin-dir" not in out

    def test_no_install_section_while_the_safety_scan_is_flagged(self, capture):
        """A flagged scan is pending review: the registry refuses public installs of it
        while its band is only "caution" and it is not withheld from listings."""
        capture(dict(DETAIL_RESPONSE, skill_safety="caution", safety_status="flagged",
                     withheld=None, withheld_reason=None, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert "Scanner: flagged" in out     # the verdict is still shown
        assert "## Install" not in out
        assert "skills add" not in out
        assert "--plugin-dir" not in out

    @pytest.mark.parametrize("fields", [
        {"skill_safety": "caution", "intent_status": "review"},
        {"skill_safety": "unreviewed", "safety_status": "unscanned"},
        {"skill_safety": "passed"},
    ], ids=["caution-intent-review", "unreviewed", "passed"])
    def test_install_section_shown_when_nothing_withholds_it(self, capture, fields):
        """Caution from an intent review still gets it: the installer asks first."""
        capture(dict(DETAIL_RESPONSE, withheld=None, withheld_reason=None,
                     **dict(INSTALL_FIELDS, **fields)))
        out = api.get_skill("semantic-search")
        assert "`decimalai skills add semantic-search`" in out

    @pytest.mark.parametrize("value", ["absent", None], ids=["key-absent", "null"])
    def test_missing_safety_status_reads_as_not_flagged(self, capture, value):
        """The registry's own default for an unset scan status is "unscanned"."""
        detail = dict(DETAIL_RESPONSE, **INSTALL_FIELDS)
        if value == "absent":
            del detail["safety_status"]
        else:
            detail["safety_status"] = value
        capture(detail)
        out = api.get_skill("semantic-search")
        assert "`decimalai skills add semantic-search`" in out

    def test_install_section_comes_before_the_body(self, capture):
        """The body can run to 4000 characters; the install line must not sit behind it."""
        capture(dict(DETAIL_RESPONSE, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert out.index("## Install") < out.index("## SKILL.md body")

    def test_steers_away_from_saving_the_body_as_skill_md(self, capture):
        """The body has no frontmatter; a SKILL.md saved from it does not load."""
        capture(dict(DETAIL_RESPONSE, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        install = out[out.index("## Install"):out.index("## SKILL.md body")]
        assert "frontmatter" in install

    def test_no_body_means_no_reference_to_one(self, capture):
        capture(dict(DETAIL_RESPONSE, body_markdown="", **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert "`decimalai skills add semantic-search`" in out
        assert "## SKILL.md body" not in out
        assert "body below" not in out

    def test_body_cut_and_notice_are_unchanged(self, capture):
        """Exactly the first 4000 characters, then the notice, then the web link."""
        body = "x" * 3999 + "y" + "z" * 6000
        capture(dict(DETAIL_RESPONSE, body_markdown=body, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert ("## SKILL.md body\n\n" + "x" * 3999 + "y" + "\n\n… [truncated]\n\nWeb page: "
                in out)
        assert "z" * 10 not in out
        assert "`decimalai skills add semantic-search`" in out

    def test_body_at_the_limit_is_verbatim_without_notice(self, capture):
        body = "x" * 4000
        capture(dict(DETAIL_RESPONSE, body_markdown=body, **INSTALL_FIELDS))
        out = api.get_skill("semantic-search")
        assert "## SKILL.md body\n\n" + body + "\n\nWeb page: " in out
        assert "[truncated]" not in out


class TestGetLeaderboard:
    def test_default_uses_leaderboard_endpoint(self, capture):
        captured = capture(LEADERBOARD_RESPONSE)
        out = api.get_leaderboard()
        req = captured[0]
        assert req.url.path == "/api/v1/registry/leaderboard"
        assert req.url.params["sort"] == "biggest_improvement"
        assert req.url.params["window_days"] == "30"
        assert "1. **sql-analyst**" in out
        assert "+66 pts pass rate" in out

    def test_a_category_is_ranked_by_the_leaderboard_itself(self, capture):
        """It went to browse with the leaderboard's sort name, which browse refuses (400)."""
        captured = capture(LEADERBOARD_RESPONSE)
        out = api.get_leaderboard(category="retrieval", sort="skill_score")
        req = captured[0]
        assert req.url.path == "/api/v1/registry/leaderboard"
        assert req.url.params["category"] == "retrieval"
        assert req.url.params["sort"] == "skill_score"
        assert "## Skills leaderboard — skill_score in 'retrieval'" in out

    def test_a_category_with_an_unknown_sort_is_refused_before_the_network(self, capture):
        captured = capture(LEADERBOARD_RESPONSE)
        assert "Unsupported sort" in api.get_leaderboard(category="retrieval", sort="installs")
        assert not captured

    def test_invalid_sort_rejected_client_side(self, capture):
        captured = capture(LEADERBOARD_RESPONSE)
        out = api.get_leaderboard(sort="installs")
        assert "Unsupported sort" in out
        assert not captured  # never hit the network


class TestServerModule:
    def test_tools_registered(self):
        """The FastMCP server exposes exactly the three read-only tools."""
        mcp_pkg = pytest.importorskip("mcp")  # noqa: F841 — skip if mcp absent
        from decimalai_mcp.server import mcp as server

        import anyio

        tools = anyio.run(server.list_tools)
        names = {t.name for t in tools}
        assert names == {"search_skills", "get_skill", "get_leaderboard"}

    def test_reports_our_version_not_the_sdks(self):
        """`initialize` must answer serverInfo with THIS package's version.

        FastMCP takes no `version` on mcp 1.x and the low-level Server it builds defaults to the
        installed `mcp` library version — so serverInfo shipped {name: "decimalai",
        version: "1.29.0"} in 0.1.0. Clients display that verbatim, so a user could not tell which
        decimalai-mcp they were running and a bug report would carry the SDK's number instead of
        ours. Pins the fix, and pins that it is OUR number specifically.
        """
        pytest.importorskip("mcp")
        import importlib.metadata as md

        from decimalai_mcp import __version__
        from decimalai_mcp.server import mcp as server

        reported = server._mcp_server.version
        assert reported == __version__, f"serverInfo reports {reported!r}, package is {__version__!r}"
        assert reported != md.version("mcp"), "serverInfo is echoing the mcp SDK version again"

    def test_get_skill_tool_docstring_points_at_url_slug(self):
        """The tool description is what the MODEL reads to pick an argument.

        It used to say "the `name` field from search results" — which 404s for
        every namespaced skill in the registry.
        """
        pytest.importorskip("mcp")
        from decimalai_mcp import server as srv

        doc = srv.get_skill.__doc__ or ""
        assert "url_slug" in doc
        assert "the `name` field from search results" not in doc

    def test_get_skill_tool_docstring_says_it_returns_the_install_command(self):
        """A model choosing a tool to install a skill reads only this description."""
        pytest.importorskip("mcp")
        from decimalai_mcp import server as srv

        doc = srv.get_skill.__doc__ or ""
        assert "install name" in doc
        assert "decimalai skills add" in doc
        # So the model reads a missing Install section as a verdict, not a gap.
        assert "blocked or withheld" in doc
        assert "flagged" in doc


class TestMainArgv:
    """`decimalai-mcp --help` used to start a stdio server and exit 0, silently."""

    def test_help_prints_usage_and_does_not_start_the_server(self, capsys, monkeypatch):
        pytest.importorskip("mcp")
        from decimalai_mcp import server as srv

        monkeypatch.setattr(srv.mcp, "run", _boom)
        for flag in ("-h", "--help"):
            srv.main([flag])
            out = capsys.readouterr().out
            assert "stdio" in out
            assert "DECIMAL_API_KEY" in out
            assert "claude mcp add decimalai -- uvx decimalai-mcp" in out

    def test_version_flag_prints_the_package_version(self, capsys, monkeypatch):
        pytest.importorskip("mcp")
        from decimalai_mcp import __version__, server as srv

        monkeypatch.setattr(srv.mcp, "run", _boom)
        srv.main(["--version"])
        assert capsys.readouterr().out.strip() == f"decimalai-mcp {__version__}"

    def test_unknown_argument_exits_2_without_serving(self, capsys, monkeypatch):
        pytest.importorskip("mcp")
        from decimalai_mcp import server as srv

        monkeypatch.setattr(srv.mcp, "run", _boom)
        with pytest.raises(SystemExit) as exc:
            srv.main(["--nope"])
        assert exc.value.code == 2
        assert "unrecognized argument" in capsys.readouterr().err

    def test_no_arguments_still_serves_over_stdio(self, monkeypatch):
        """The default path is the whole product — it must be untouched."""
        pytest.importorskip("mcp")
        from decimalai_mcp import server as srv

        calls: list[int] = []
        monkeypatch.setattr(srv.mcp, "run", lambda: calls.append(1))
        srv.main([])
        assert calls == [1]


# ── What the skill's page says, said the same way (2026-11-10 cold walk) ─────
#
# A stranger compared this server's output with the skill page and found five things that
# disagreed: July-era stored counts with no caveat (browser-automation "2/23" where its
# checks say 23 of 23), "last 30 days" on lifetime stats, a benchmark of an older version
# shown as current, a "v2" label on a v3 score, and "Installs" for what the page calls Forks.

AUGUST_BENCH = {
    "verdict": "pass", "total_cases": 23, "passed_cases": 23, "lift_cases": 23,
    "lift_pass_rate_pct": 100.0, "pass_rate_delta_pts": 69.57, "never_hurt": True,
    "completed_at": "2026-09-23T05:49:35+00:00",
}
JULY_BENCH = {
    "verdict": "mixed", "total_cases": 23, "passed_cases": 2, "lift_cases": 23,
    "lift_pass_rate_pct": 8.7, "pass_rate_delta_pts": 4.3, "never_hurt": False,
    "completed_at": "2026-07-28T10:00:00+00:00",
}


class TestTheSkillPagesNumbers:
    def test_a_july_benchmark_shows_no_count(self, capture):
        capture(dict(DETAIL_RESPONSE, benchmark_summary=JULY_BENCH))
        out = api.get_skill("browser-automation")
        assert api.PRE_AUGUST_NOTE in out
        assert "2/23" not in out and "2 of 23" not in out
        assert "+4.3" not in out

    def test_a_july_benchmark_shows_no_lift_in_search_either(self, capture):
        item = dict(BROWSE_RESPONSE["items"][0], benchmark_summary=JULY_BENCH)
        capture(dict(BROWSE_RESPONSE, items=[item]))
        out = api.search_skills("browser automation")
        assert "tested before August 2026, no count shown" in out
        assert "+4.3" not in out

    def test_an_august_or_later_benchmark_reads_like_the_page(self, capture):
        capture(dict(DETAIL_RESPONSE, benchmark_summary=AUGUST_BENCH))
        out = api.get_skill("playwright-cli")
        assert "Passed 23 of 23 test cases with the skill, and 7 of 23 without it (+69.6 pts)." in out

    def test_a_rate_paired_with_the_wrong_count_prints_rates_not_counts(self, capture):
        # open-gsd-gsd-workspace as the registry sent it before 2026-11-11: 12 and 5 of 23,
        # paired with the 16 comparable cases. Multiplying printed "8 of 16 ... 3 of 16".
        bench = dict(AUGUST_BENCH, lift_cases=16, lift_pass_rate_pct=52.2, pass_rate_delta_pts=30.43)
        capture(dict(DETAIL_RESPONSE, benchmark_summary=bench))
        out = api.get_skill("open-gsd-gsd-workspace")
        assert "8 of 16" not in out and "3 of 16" not in out
        assert "Passed 52.2% of its test cases with the skill, and 21.8% without it (+30.4 pts)." in out

    def test_the_fixed_pairing_prints_the_real_counts(self, capture):
        bench = dict(AUGUST_BENCH, lift_cases=23, lift_pass_rate_pct=52.2, pass_rate_delta_pts=30.43)
        capture(dict(DETAIL_RESPONSE, benchmark_summary=bench))
        out = api.get_skill("open-gsd-gsd-workspace")
        assert "Passed 12 of 23 test cases with the skill, and 5 of 23 without it (+30.4 pts)." in out

    def test_says_when_the_benchmark_measured_an_older_version(self, capture):
        capture(dict(DETAIL_RESPONSE, benchmark_summary=AUGUST_BENCH, latest_version_number=5,
                     benchmark_models=[{"headline": True, "version_number": 4}]))
        out = api.get_skill("playwright-cli")
        assert "This benchmark measured v4; the latest version is v5." in out

    def test_no_version_line_when_the_latest_version_was_measured(self, capture):
        capture(dict(DETAIL_RESPONSE, benchmark_summary=AUGUST_BENCH, latest_version_number=5,
                     benchmark_models=[{"headline": True, "version_number": 5}]))
        assert "This benchmark measured" not in api.get_skill("playwright-cli")

    def test_skillscore_has_no_formula_label_and_uses_the_pages_scale(self, capture):
        capture(DETAIL_RESPONSE)
        out = api.get_skill("semantic-search")
        assert "- SkillScore: 85/100" in out
        assert "v2" not in out.split("## Evidence")[1].split("\n")[1]

    def test_usage_uses_the_pages_words(self, capture):
        capture(dict(DETAIL_RESPONSE, download_count=85, downloads_since="2026-10-15", fork_count=0,
                     usage_scope={"total_uses": 12}))
        out = api.get_skill("semantic-search")
        assert "Downloads: 85 (since 2026-10-15); Agent runs: 12; rating:" in out
        assert "Installs:" not in out
        assert "Forks" not in out       # the page leaves out a zero


class TestLeaderboardWindow:
    def test_the_title_says_what_the_registry_says(self, capture):
        capture(dict(LEADERBOARD_RESPONSE, usage_scope={"quality_scope": "lifetime"}))
        out = api.get_leaderboard()
        assert "## Skills leaderboard — biggest_improvement (lifetime)" in out
        assert "last 30 days" not in out

    def test_no_window_claim_when_the_registry_names_none(self, capture):
        capture(LEADERBOARD_RESPONSE)
        out = api.get_leaderboard()
        assert "last 30 days" not in out and "days)" not in out

    def test_an_unknown_category_says_what_to_use(self, capture):
        capture({"items": [], "has_next": False})
        out = api.get_leaderboard(category="retrieval")
        assert "No ranked skills in category 'retrieval'" in out
        assert "documentation" in out


class TestSearchHeader:
    def test_more_results_are_not_reported_as_all(self, capture):
        capture({"items": BROWSE_RESPONSE["items"], "has_next": True, "next_cursor": "x"})
        out = api.search_skills("semantic search")
        assert "(first 1; more match)" in out
        assert "(1 of 1)" not in out
