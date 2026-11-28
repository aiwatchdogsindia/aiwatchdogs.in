from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.safety import scan_forbidden_terms
from .packs import pack

LINT_FILE = Path(__file__).resolve().parent / "lint.json"
_NUM_RE = re.compile(r"\d+(?:[.,  '’]\d+)*")
ALWAYS_OK = {2005.0, 2017.0, 3.0, 10.0, 30.0}


@lru_cache(maxsize=1)
def _lint_words() -> dict[str, list[str]]:
    try:
        data = json.loads(LINT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: [str(w).lower() for w in v] for k, v in data.items() if isinstance(v, list)}


def ascii_digits(text: str) -> str:
    out = []
    for ch in text:
        if ch.isdecimal() and not ("0" <= ch <= "9"):
            out.append(str(unicodedata.decimal(ch)))
        else:
            out.append(ch)
    return "".join(out)


def numbers_in(text: str, lang: str = "en") -> list[float]:
    dec = str((pack(lang).get("num") or {}).get("dec", "."))
    found: list[float] = []
    for raw in _NUM_RE.findall(ascii_digits(text or "")):
        s = raw.replace(" ", "").replace(" ", "").replace("'", "").replace("’", "")
        if dec == ",":
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        if s.count(".") > 1:
            parts = s.split(".")
            s = "".join(parts[:-1]) + "." + parts[-1]
        try:
            found.append(float(s))
        except ValueError:
            continue
    return found


_MONEY_KEY = re.compile(
    r"(budget|revised|actual|unspent|cut|amount|value|inr|crore|lakh|exposure|cost|fund|compensation|^be$|^re$|^be_next$)",
    re.IGNORECASE,
)
_PCT_KEY = re.compile(r"(pct|percent|share|rate)", re.IGNORECASE)
_UNIT = {"crore": 1e7, "lakh": 1e5, "thousand": 1e3, "rupee": 1.0, "inr": 1.0}
_PCT_SIGNS = ("%", "٪", "％")
_CURRENCY = re.compile(r"(₹|rs\.?|inr)\s*$", re.IGNORECASE)


@dataclass
class Allowed:
    plain: set[float] = field(default_factory=set)
    money: set[float] = field(default_factory=set)
    pct: set[float] = field(default_factory=set)


def _stem(word: str) -> str:
    w = unicodedata.normalize("NFC", word).lower()
    return w[: max(3, len(w) - 2)]


@lru_cache(maxsize=1)
def _unit_stems() -> tuple[tuple[str, float], ...]:
    from .packs import _all_packs

    stems: dict[str, float] = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "million": 1e6, "billion": 1e9}
    for data in _all_packs().values():
        money = data.get("money") or {}
        for key, mult in (("crore", 1e7), ("lakh", 1e5)):
            word = str(money.get(key) or "").strip()
            if word:
                stems.setdefault(_stem(word), mult)
    return tuple(sorted(stems.items(), key=lambda kv: -len(kv[0])))


def _unit_after(rest: str) -> float | None:
    tail = unicodedata.normalize("NFC", rest.lstrip(" \u00a0\u202f")).lower()
    for stem, mult in _unit_stems():
        if tail.startswith(stem):
            nxt = tail[len(stem) : len(stem) + 1]
            if nxt.isalpha() and (len(stem) < 3 or (stem.isascii() and len(stem) <= 3)):
                continue
            return mult
    return None


def typed_numbers(text: str, lang: str = "en") -> list[tuple[str, float, float]]:
    src = ascii_digits(text or "")
    out: list[tuple[str, float, float]] = []
    for m in _NUM_RE.finditer(src):
        vals = numbers_in(m.group(0), lang)
        if not vals:
            continue
        v = vals[0]
        before, after = src[max(0, m.start() - 5) : m.start()], src[m.end() : m.end() + 24]
        if after.lstrip(" \u00a0\u202f")[:1] in _PCT_SIGNS or before.rstrip()[-1:] in _PCT_SIGNS:
            out.append(("pct", v, v))
            continue
        unit = _unit_after(after)
        currency = bool(_CURRENCY.search(before))
        if unit is None and currency and after.lstrip(" \u00a0\u202f")[:1].lower() == "l" and not after.lstrip(" \u00a0\u202f")[1:2].isalpha():
            unit = 1e5
        if unit is not None:
            out.append(("money", v, v * unit))
        elif currency:
            out.append(("money", v, v))
        else:
            out.append(("num", v, v))
    return out


def _walk_numbers(obj: Any, out: Allowed, key: str = "", unit: float = 1e7, depth: int = 0, declared: bool = False) -> None:
    if depth > 8 or isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        v = abs(float(obj))
        if not math.isfinite(v):
            return
        out.plain.add(v)
        if key and _PCT_KEY.search(key):
            out.pct.add(v)
        elif key and _MONEY_KEY.search(key):
            low = key.lower()
            if "inr" in low or "rupee" in low:
                out.money.add(v)
            elif declared:
                out.money.add(v * unit)
            else:
                out.money.add(v if v >= 1e5 else v * unit)
    elif isinstance(obj, str):
        for kind, v, inr in typed_numbers(obj):
            out.plain.add(v)
            if kind == "money":
                out.money.add(inr)
            elif kind == "pct":
                out.pct.add(v)
    elif isinstance(obj, dict):
        named = _UNIT.get(str(obj.get("unit") or "").lower())
        u, dec = (named, True) if named is not None else (unit, declared)
        for k, v in obj.items():
            _walk_numbers(v, out, str(k), u, depth + 1, dec)
    elif isinstance(obj, (list, tuple)):
        for v in obj[:200]:
            _walk_numbers(v, out, key, unit, depth + 1, declared)


def allowed_numbers(*sources: Any) -> Allowed:
    out = Allowed(plain=set(ALWAYS_OK))
    for s in sources:
        _walk_numbers(s, out)
    return out


def number_ok(n: float, allowed: set[float] | Allowed) -> bool:
    values = allowed.plain | allowed.pct if isinstance(allowed, Allowed) else allowed
    n = abs(n)
    for a in values:
        if a == 0:
            if n == 0:
                return True
            continue
        if abs(n - a) <= 1e-9:
            return True
        for d in (0, 1, 2):
            if abs(round(a, d) - n) <= 1e-9:
                return True
        if abs(n - a) / abs(a) <= 0.005:
            return True
    return False


def money_ok(shown: float, inr: float, allowed: Allowed) -> bool:
    unit = inr / shown if shown else 1.0
    for a in allowed.money:
        if a <= 0:
            continue
        if abs(inr - a) / a <= 0.006:
            return True
        scaled = a / unit
        if any(abs(round(scaled, d) - shown) <= 1e-9 for d in (0, 1, 2)):
            return True
    return False


def bad_numbers(text: str, lang: str, allowed: set[float] | Allowed) -> list[float]:
    if not isinstance(allowed, Allowed):
        allowed = Allowed(plain=set(allowed))
    bad: list[float] = []
    for kind, v, inr in typed_numbers(text, lang):
        if kind == "money":
            ok = money_ok(v, inr, allowed)
        elif kind == "pct":
            ok = number_ok(v, allowed.pct)
        else:
            ok = number_ok(v, allowed.plain)
        if not ok:
            bad.append(v)
    return bad


_SPLIT = re.compile(r"[\s.,;:!?\"'“”‘’()\[\]{}«»„।॥،؟、。，！？：；…/\-]+")
_SUBSTRING_LANGS = {"zh", "ja"}


def lint_hits(text: str, lang: str) -> list[str]:
    hits = list(scan_forbidden_terms(text or ""))
    low = unicodedata.normalize("NFKC", text or "").lower()
    words = [unicodedata.normalize("NFKC", w) for w in _lint_words().get(lang, []) if w]
    if lang in _SUBSTRING_LANGS:
        hits.extend(w for w in words if w in low)
    else:
        tokens = [t for t in _SPLIT.split(low) if t]
        for w in words:
            parts = [t for t in _SPLIT.split(w) if t]
            if len(parts) <= 1:
                if any(t.startswith(w) for t in tokens):
                    hits.append(w)
            elif _has_phrase(tokens, parts):
                hits.append(w)
    return hits


def _has_phrase(tokens: list[str], parts: list[str]) -> bool:
    n = len(parts)
    for i in range(len(tokens) - n + 1):
        if tokens[i : i + n - 1] == parts[:-1] and tokens[i + n - 1].startswith(parts[-1]):
            return True
    return False


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n)


def grade_level(text: str) -> float:
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", text or "") if s.strip()]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text or "")
    if not sentences or not words:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return 0.39 * (len(words) / len(sentences)) + 11.8 * (syl / len(words)) - 15.59


LIMITS = {"head": 160, "what": 700, "why": 420, "ask": 220, "lede": 900}


def check_entry(entry: Any, lang: str, allowed: set[float], fields: tuple[str, ...]) -> list[str]:
    problems: list[str] = []
    if not isinstance(entry, dict):
        return ["not an object"]
    for key in fields:
        val = entry.get(key)
        if not isinstance(val, str) or not val.strip():
            problems.append(f"{key}: empty")
            continue
        if len(val) > LIMITS.get(key, 700):
            problems.append(f"{key}: too long")
        if "{" in val or "}" in val:
            problems.append(f"{key}: template braces")
        nums = bad_numbers(val, lang, allowed)
        if nums:
            problems.append(f"{key}: numbers not in the source {nums[:3]}")
        hits = lint_hits(val, lang)
        if hits:
            problems.append(f"{key}: loaded words {hits[:3]}")
    if lang == "en" and not problems:
        body = " ".join(str(entry.get(k, "")) for k in fields if k != "head")
        g = grade_level(body)
        if g > 10.0:
            problems.append(f"reading grade {g:.1f} is above 10")
    return problems
