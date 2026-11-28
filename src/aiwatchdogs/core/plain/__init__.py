"""Plain-language headlines and one-line briefs for public findings.

Every finding keeps its stored ``title`` and ``summary`` untouched. This package adds two
reader-facing fields, built only from the deterministic fields already stored on the finding:

* ``headline``: one short news-style line saying what the record shows.
* ``brief``: one or two short sentences with the key figures and dates.

Nothing here calls a model or reads the network, so the same finding always yields the same text.
Each check type has its own writer module exposing ``render(finding) -> (headline, brief)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib import import_module
from typing import Any

from ._common import BRIEF_MAX, HEADLINE_MAX, clean, clip, generic

logger = logging.getLogger(__name__)

Writer = Callable[[dict[str, Any]], tuple[str, str]]

_WRITER_MODULES: dict[str, str] = {
    "P1": "p1",
    "P5": "p5",
    "P6": "p6",
    "P7": "p7",
    "P8": "p8",
    "P9": "p9",
    "P10": "p10",
    "UB1": "ub1",
    "OR1": "or1",
}
_writers: dict[str, Writer | None] = {}


def _writer_for(code: str) -> Writer | None:
    if code not in _writers:
        writer: Writer | None = None
        module_name = _WRITER_MODULES.get(code)
        if module_name:
            try:
                writer = import_module(f".{module_name}", __name__).render
            except Exception:  # a missing or broken writer must never take the findings API down
                logger.exception("plain-language writer for %s failed to load", code)
        _writers[code] = writer
    return _writers[code]


def plain_brief(finding: dict[str, Any]) -> dict[str, str]:
    """Return ``{"headline": ..., "brief": ...}`` for one serialized finding."""
    code = clean(finding.get("methodology_code")).upper()
    headline = brief = ""
    writer = _writer_for(code)
    if writer is not None:
        try:
            headline, brief = writer(finding)
        except Exception:
            logger.exception("plain-language writer for %s failed on finding %s", code, finding.get("id"))
            headline = brief = ""
    if not clean(headline):
        headline, brief = generic(finding)
    headline = clip(headline, HEADLINE_MAX)
    brief = clip(brief, BRIEF_MAX)
    if brief and brief[-1] not in ".…!?":
        brief += "."
    return {"headline": headline, "brief": brief}


__all__ = ["BRIEF_MAX", "HEADLINE_MAX", "plain_brief"]
