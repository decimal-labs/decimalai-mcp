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
        assert "SkillScore 0.85" in out
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

    def test_category_falls_back_to_ranked_browse(self, capture):
        captured = capture(BROWSE_RESPONSE)
        api.get_leaderboard(category="retrieval", sort="skill_score")
        req = captured[0]
        assert req.url.path == "/api/v1/registry/skills"
        assert req.url.params["view"] == "ranks"
        assert req.url.params["category"] == "retrieval"

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
