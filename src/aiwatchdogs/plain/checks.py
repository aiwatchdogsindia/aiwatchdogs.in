from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.safety import scan_forbidden_terms
from .packs import pack

LINT_FILE = Path(__file__).resolve().parent / "lint.json"
_NUM_RE = re.compile(r"\d+(?:[.,  '’]\d+)*")
_DISPLAY = (1.0, 1e7, 1e5, 1e6, 1e9, 1e8, 1e4, 1e3)
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
_UNIT = {"crore": 1e7, "lakh": 1e5, "thousand": 1e3, "rupee": 1.0, "inr": 1.0}


def _money_forms(inr: float, out: set[float]) -> None:
    for div in _DISPLAY:
        out.add(abs(inr) / div)


def _walk_numbers(obj: Any, out: set[float], key: str = "", unit: float = 1e7, depth: int = 0) -> None:
    if depth > 8 or isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        v = float(obj)
        if not math.isfinite(v):
            return
        out.add(abs(v))
        if key and _MONEY_KEY.search(key):
            _money_forms(v if abs(v) >= 1e5 or "inr" in key.lower() or "rupee" in key.lower() else v * unit, out)
    elif isinstance(obj, str):
        out.update(numbers_in(obj))
    elif isinstance(obj, dict):
        u = _UNIT.get(str(obj.get("unit") or "").lower(), unit)
        for k, v in obj.items():
            _walk_numbers(v, out, str(k), u, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj[:200]:
            _walk_numbers(v, out, key, unit, depth + 1)


def allowed_numbers(*sources: Any) -> set[float]:
    out = set(ALWAYS_OK)
    for s in sources:
        _walk_numbers(s, out)
    return out


def number_ok(n: float, allowed: set[float]) -> bool:
    n = abs(n)
    for a in allowed:
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


def bad_numbers(text: str, lang: str, allowed: set[float]) -> list[float]:
    return [n for n in numbers_in(text, lang) if not number_ok(n, allowed)]


_SPLIT = re.compile(r"[\s.,;:!?\"'“”‘’()\[\]{}«»„।॥،؟、。，！？：；…/\-]+")
_SUBSTRING_LANGS = {"zh", "ja"}


def lint_hits(text: str, lang: str) -> list[str]:
    hits = list(scan_forbidden_terms(text or ""))
    low = unicodedata.normalize("NFKC", text or "").lower()
    words = [w for w in _lint_words().get(lang, []) if w]
    if lang in _SUBSTRING_LANGS:
        hits.extend(w for w in words if w in low)
    else:
        tokens = [t for t in _SPLIT.split(low) if t]
        hits.extend(w for w in words if any(t.startswith(w) for t in tokens))
    return hits


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
