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
