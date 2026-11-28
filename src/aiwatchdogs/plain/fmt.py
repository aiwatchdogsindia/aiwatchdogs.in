from __future__ import annotations

import math
from typing import Any

from .packs import pack

CRORE = 1e7
LAKH = 1e5


def _num_cfg(lang: str) -> dict[str, Any]:
    cfg = pack(lang).get("num") or {}
    return {
        "dec": str(cfg.get("dec", ".")),
        "group": str(cfg.get("group", ",")),
        "grouping": str(cfg.get("grouping", "western")),
        "pct": str(cfg.get("pct", "{n}%")),
    }


def _group_int(digits: str, sep: str, style: str) -> str:
    if len(digits) <= 3:
        return digits
    if style == "indian":
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        return sep.join(parts + [tail])
    parts = []
    while len(digits) > 3:
        parts.insert(0, digits[-3:])
        digits = digits[:-3]
    parts.insert(0, digits)
    return sep.join(parts)


def number(value: Any, lang: str, decimals: int = 0) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v):
        return ""
    cfg = _num_cfg(lang)
    neg = v < 0
    s = f"{abs(v):.{decimals}f}"
    whole, _, frac = s.partition(".")
    frac = frac.rstrip("0")
    out = _group_int(whole, cfg["group"], cfg["grouping"])
    if frac:
        out = f"{out}{cfg['dec']}{frac}"
    return f"-{out}" if neg else out


def percent(value: Any, lang: str) -> str:
    try:
        v = abs(float(value))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v):
        return ""
    n = number(v, lang, 1 if v < 100 else 0)
    return _num_cfg(lang)["pct"].replace("{n}", n)


def money(inr: Any, lang: str) -> str:
    try:
        v = abs(float(inr))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v) or v <= 0:
        return ""
    cfg = pack(lang).get("money") or {}
    style = str(cfg.get("style", "indian"))
    space = "" if cfg.get("tight") else " "
    sym = str(cfg.get("symbol", "₹"))

    def unit(amount: float, word: str) -> str:
        dec = 2 if amount < 10 else (1 if amount < 100 else 0)
        n = number(amount, lang, dec)
        if not word:
            return f"{sym}{n}"
        pattern = str(cfg.get("pattern", "{sym}{n}{space}{unit}"))
        return pattern.replace("{sym}", sym).replace("{n}", n).replace("{space}", space).replace("{unit}", word)

    if style == "cjk":
        if v >= 1e8:
            return unit(v / 1e8, str(cfg.get("yi", "")))
        if v >= 1e4:
            return unit(v / 1e4, str(cfg.get("wan", "")))
        return unit(v, "")
    if style == "intl":
        if v >= 1e9:
            return unit(v / 1e9, str(cfg.get("billion", "")))
        if v >= 1e6:
            return unit(v / 1e6, str(cfg.get("million", "")))
        return unit(v, "")
    if v >= CRORE:
        return unit(v / CRORE, str(cfg.get("crore", "crore")))
    if v >= LAKH:
        return unit(v / LAKH, str(cfg.get("lakh", "lakh")))
    return unit(v, "")


def word_count(text: str) -> int:
    cjk = sum(1 for ch in text if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    rest = "".join(" " if ("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿") else ch for ch in text)
    return len(rest.split()) + math.ceil(cjk / 2)


WORDS_PER_MINUTE = {
    "hi": 250, "ur": 250, "bn": 230, "mr": 220, "gu": 230, "pa": 250, "or": 220, "as": 220,
    "ta": 150, "te": 160, "kn": 160, "ml": 140, "ru": 180, "de": 190, "id": 190, "ar": 180,
}


def reading_minutes(text: str, lang: str = "en") -> int:
    return max(1, round(word_count(text) / WORDS_PER_MINUTE.get(lang, 200)))
