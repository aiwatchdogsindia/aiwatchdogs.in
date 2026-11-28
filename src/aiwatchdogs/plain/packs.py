from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PACK_DIR = Path(__file__).resolve().parent / "packs"
DEFAULT_LANG = "en"
LANG_ORDER = (
    "en", "hi", "bn", "mr", "te", "ta", "gu", "ur", "kn", "or", "ml", "pa", "as",
    "es", "fr", "ar", "pt", "ru", "zh", "id", "de", "ja",
)
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_LANG_RE = re.compile(r"^[a-z]{2,3}$")


@lru_cache(maxsize=1)
def _all_packs() -> dict[str, dict[str, Any]]:
    packs: dict[str, dict[str, Any]] = {}
    for path in sorted(PACK_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        packs[str(data["code"])] = data
    return packs


def available_languages() -> list[dict[str, str]]:
    packs = _all_packs()
    order = [c for c in LANG_ORDER if c in packs] + sorted(c for c in packs if c not in LANG_ORDER)
    return [
        {
            "code": c,
            "name": str(packs[c]["name"]),
            "native": str(packs[c]["native"]),
            "dir": str(packs[c].get("dir", "ltr")),
            "bcp47": str(packs[c].get("bcp47", c)),
        }
        for c in order
    ]


def resolve_lang(raw: str | None) -> str:
    packs = _all_packs()
    for part in str(raw or "").replace("_", "-").lower().split(","):
        code = part.split(";")[0].strip().split("-")[0]
        if _LANG_RE.match(code) and code in packs:
            return code
    return DEFAULT_LANG


def _lookup(tree: Any, path: tuple[str, ...]) -> Any:
    node = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def text(lang: str, *path: str) -> str:
    packs = _all_packs()
    val = _lookup(packs.get(lang, {}), path)
    if not isinstance(val, str) or not val.strip():
        val = _lookup(packs[DEFAULT_LANG], path)
    return val if isinstance(val, str) else ""


def section(lang: str, *path: str) -> dict[str, Any]:
    packs = _all_packs()
    base = _lookup(packs[DEFAULT_LANG], path)
    out = dict(base) if isinstance(base, dict) else {}
    local = _lookup(packs.get(lang, {}), path)
    if isinstance(local, dict):
        out.update({k: v for k, v in local.items() if isinstance(v, str) and v.strip()})
    return out


def pack(lang: str) -> dict[str, Any]:
    packs = _all_packs()
    return packs.get(lang) or packs[DEFAULT_LANG]


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template or ""))


def fill(template: str, slots: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in slots or slots[key] in (None, ""):
            raise KeyError(key)
        return str(slots[key])

    return re.sub(r"[ \t\r\n]+", " ", _PLACEHOLDER.sub(sub, template)).strip()


def flatten(tree: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(tree, dict):
        for k, v in tree.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(tree, str):
        out[prefix] = tree
    return out
