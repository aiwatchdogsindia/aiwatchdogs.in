"""Console encoding tests for the CLI entrypoint."""

from __future__ import annotations

import io
import sys

import pytest

from aiwatchdogs.cli.main import ensure_utf8_streams


def test_ensure_utf8_streams_reconfigures_cp1252_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    ensure_utf8_streams()

    assert stream.encoding is not None
    assert stream.encoding.replace("-", "").lower() == "utf8"
    stream.write("₹")
    stream.flush()
    assert buffer.getvalue().decode("utf-8") == "₹"
