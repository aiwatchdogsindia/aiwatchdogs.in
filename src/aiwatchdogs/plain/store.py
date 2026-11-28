from __future__ import annotations

import json
from typing import Any


def load_texts(db: Any, keys: list[str]) -> dict[str, dict[str, Any]]:
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with db.read() as conn:
            for i in range(0, len(keys), 200):
                chunk = keys[i : i + 200]
                ph = ",".join("?" for _ in chunk)
                for r in conn.execute(
                    f"SELECT key, source_hash, model, payload_json FROM plain_texts WHERE key IN ({ph})", chunk
                ).fetchall():
                    try:
                        payload = json.loads(r["payload_json"] or "{}")
                    except ValueError:
                        payload = {}
                    out[str(r["key"])] = {"hash": str(r["source_hash"]), "model": str(r["model"]), "langs": payload}
    except Exception:
        return {}
    return out
