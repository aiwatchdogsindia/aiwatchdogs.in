"""SHA-256-pinned disk cache entries outlive the TTL; unpinned entries still expire."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aiwatchdogs.connectors import loader
from aiwatchdogs.core.safety import compute_sha256

BODY = b"department,budget,actual\nHealth,100,80\n"
URL = "https://www.indiabudget.gov.in/doc/eb/test-pinned-ttl.csv"


def _seed_old_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age: timedelta) -> str:
    monkeypatch.setenv("AIWATCHDOGS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AIWATCHDOGS_CACHE_TTL_SECONDS", "3600")
    digest = compute_sha256(BODY)
    fetched_at = (datetime.now(timezone.utc) - age).isoformat(timespec="seconds").replace("+00:00", "Z")
    loader._persist_raw_cache_if_clean(URL, BODY, "text/csv", digest, fetched_at, has_pii=False)
    return digest


def test_pinned_entry_older_than_ttl_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    digest = _seed_old_entry(tmp_path, monkeypatch, timedelta(days=2))
    hit = loader._read_validated_cache(URL, digest, 10_000_000)
    assert hit is not None and hit[0] == BODY


def test_unpinned_entry_older_than_ttl_still_expires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_old_entry(tmp_path, monkeypatch, timedelta(days=2))
    assert loader._read_validated_cache(URL, None, 10_000_000) is None


def test_pinned_entry_with_wrong_digest_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_old_entry(tmp_path, monkeypatch, timedelta(days=2))
    assert loader._read_validated_cache(URL, "0" * 64, 10_000_000) is None
