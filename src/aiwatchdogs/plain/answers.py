from __future__ import annotations

import json
from typing import Any

from . import fmt
from .explain import explain_finding
from .packs import fill, text

_VISIBLE = "COALESCE(quarantined, 0) = 0 AND status != 'quarantined' AND COALESCE(is_demo_sample, 0) = 0"
_COLS = (
    "id, mission_slug, methodology_code, title, entity_name, jurisdiction, fiscal_year, "
    "monetary_exposure_inr, anomaly_metrics_json, rule_triggered, status, confirm_count, required_quorum"
)


def project_answers(db: Any, slugs: list[str], lang: str) -> dict[str, dict[str, Any]]:
    slugs = [s for s in dict.fromkeys(slugs) if s]
    if not slugs:
        return {}
    out: dict[str, dict[str, Any]] = {}
    ph = ",".join("?" for _ in slugs)
    with db.read() as conn:
        counts: dict[str, dict[str, int]] = {}
        for r in conn.execute(
            f"SELECT mission_slug, status, COUNT(*) AS c FROM findings WHERE {_VISIBLE} AND mission_slug IN ({ph}) "
            "GROUP BY mission_slug, status",
            slugs,
        ).fetchall():
            c = counts.setdefault(str(r["mission_slug"]), {"flagged": 0, "verified": 0})
            c["flagged"] += int(r["c"] or 0)
            if str(r["status"]) in ("verified", "rti_filed"):
                c["verified"] += int(r["c"] or 0)
        best: dict[str, Any] = {}
        for r in conn.execute(
            f"SELECT {_COLS} FROM findings WHERE {_VISIBLE} AND status IN ('verified', 'rti_filed') "
            f"AND mission_slug IN ({ph}) ORDER BY monetary_exposure_inr DESC, COALESCE(verified_at, created_at) DESC",
            slugs,
        ).fetchall():
            best.setdefault(str(r["mission_slug"]), r)
        tasks = {
            str(r["slug"]): (int(r["completed_tasks"] or 0), int(r["total_tasks"] or 0))
            for r in conn.execute(f"SELECT slug, completed_tasks, total_tasks FROM missions WHERE slug IN ({ph})", slugs).fetchall()
        }
    for slug in slugs:
        row = best.get(slug)
        if row is not None:
            f = dict(row)
            try:
                f["anomaly_metrics"] = json.loads(f.pop("anomaly_metrics_json") or "{}")
            except ValueError:
                f["anomaly_metrics"] = {}
            card = explain_finding(f, lang)
            if card:
                out[slug] = {"answer": card["head"], "finding_id": str(f["id"])}
                continue
        c = counts.get(slug, {"flagged": 0, "verified": 0})
        if c["flagged"]:
            out[slug] = {"answer": text(lang, "news", "state_none"), "finding_id": None}
        else:
            done, total = tasks.get(slug, (0, 0))
            if total:
                out[slug] = {
                    "answer": fill(text(lang, "news", "state_tasks"), {"done": fmt.number(done, lang) or "0", "total": fmt.number(total, lang)}),
                    "finding_id": None,
                }
    return out
