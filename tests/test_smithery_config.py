"""Checks on the Smithery listing (`smithery.yaml`).

`smithery.yaml` is a PUBLISHED artifact: Smithery reads it to decide how to
start this server for a user, and nothing else in the suite touches it. The
failure it guards against is silent — a launch command that only works on a
machine where the console script is already on PATH looks fine in review and
fails in Smithery's runtime.

The file is not shipped in the sdist (see the hatch sdist allow-list in
pyproject.toml), so these tests skip when run from an unpacked tarball rather
than the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is a dev-only dependency")

SMITHERY_PATH = Path(__file__).resolve().parent.parent / "smithery.yaml"
README_PATH = Path(__file__).resolve().parent.parent / "README.md"

pytestmark = pytest.mark.skipif(
    not SMITHERY_PATH.is_file(),
    reason="smithery.yaml is repo-only; not present in an sdist build",
)


@pytest.fixture(scope="module")
def smithery_text() -> str:
    return SMITHERY_PATH.read_text()


@pytest.fixture(scope="module")
def smithery(smithery_text: str) -> dict:
    return yaml.safe_load(smithery_text)


def test_smithery_yaml_parses(smithery: dict) -> None:
    start = smithery["startCommand"]
    assert start["type"] == "stdio"
    assert start["configSchema"]["properties"]["decimalApiKey"]["type"] == "string"
    # The API key is optional — every tool reads public endpoints anonymously.
    assert start["configSchema"]["required"] == []


def test_command_matches_the_documented_uvx_invocation(smithery: dict) -> None:
    """The launch command must be `uvx decimalai-mcp`, as the README documents.

    A bare ``command: "decimalai-mcp"`` assumes the console script is already
    installed globally in whatever environment Smithery starts the server in.
    """
    fn = smithery["startCommand"]["commandFunction"]
    assert re.search(r'command:\s*"uvx"', fn), fn
    assert re.search(r'args:\s*\[\s*"decimalai-mcp"\s*\]', fn), fn


def test_command_function_still_passes_the_optional_api_key(smithery: dict) -> None:
    fn = smithery["startCommand"]["commandFunction"]
    assert "config.decimalApiKey" in fn
    assert "DECIMAL_API_KEY" in fn
    # Absent key → empty env, not an env var set to undefined.
    assert re.search(r"config\.decimalApiKey\s*\?.*:\s*\{\}", fn, re.S), fn


def test_readme_documents_the_same_invocation() -> None:
    assert "uvx decimalai-mcp" in README_PATH.read_text()


def test_no_unverified_schema_disclaimer(smithery_text: str) -> None:
    """Comments in a published config are read by users; don't ship self-doubt.

    The file used to open with "verify current schema at ... before submitting",
    which tells every reader the listing was never checked.
    """
    lowered = smithery_text.lower()
    for phrase in ("verify current schema", "before submitting", "not verified"):
        assert phrase not in lowered, f"{phrase!r} should not appear in smithery.yaml"
