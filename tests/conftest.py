"""Shared pytest configuration for AI Watchdogs India test suite."""

from __future__ import annotations

import warnings

import pytest

_STARLETTE_HTTPX_DEPRECATION = r"Using `httpx` with `starlette\.testclient` is deprecated.*"

# Starlette 1.10 emits a deprecation warning at import time when httpx (0.28) is used
# instead of httpx2. Filter that specific upstream import warning cleanly during both
# collection and test execution.
warnings.filterwarnings("ignore", message=_STARLETTE_HTTPX_DEPRECATION)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "filterwarnings",
        f"ignore:{_STARLETTE_HTTPX_DEPRECATION}",
    )
    config.addinivalue_line(
        "markers",
        "production_missions: run with production mission behaviour (M1–M8 archived, real projects/ loaded)",
    )


@pytest.fixture(autouse=True)
def _hub_fixture_missions(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most hub tests use the original M1–M8 missions as fixtures for desk mechanics.

    Production archives them in favour of community projects; tests opt into that
    behaviour with ``@pytest.mark.production_missions``. Community project files are
    isolated to an empty directory unless a test points AIWATCHDOGS_PROJECTS_DIR elsewhere.
    """
    # Desk-mechanics tests pick a project by name; production hubs ignore that choice.
    if not request.node.get_closest_marker("production_missions"):
        monkeypatch.setenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", "1")
    if request.node.get_closest_marker("production_missions") is None:
        monkeypatch.setenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", "1")
        monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(tmp_path_factory.mktemp("no-projects")))
