from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..core.safety import scrub_pii_in_obj
from . import fmt
from .explain import KINDS, explain_finding, kind_for, labels
from .packs import fill, pack, section, text

IST = timezone(timedelta(hours=5, minutes=30))
EDITION_VERSION = 1
CACHE_PREFIX = f"news:v{EDITION_VERSION}:"
FIRST_DATE = date(2025, 1, 1)
MAX_ARCHIVE = 60
SITE = "https://aiwatchdogs.in"
_VISIBLE = "COALESCE(quarantined, 0) = 0 AND status != 'quarantined' AND COALESCE(is_demo_sample, 0) = 0"
_SNAP_COLS = (
    "id, mission_slug, methodology_code, title, entity_name, jurisdiction, fiscal_year, "
    "monetary_exposure_inr, anomaly_metrics_json, rule_triggered, status, confirm_count, "
    "required_quorum, source_url, verified_at, created_at"
)
EXPLAINERS = ("budget_words", "tenders", "cag", "rti", "quorum", "assurance", "release")
SPOTLIGHT = tuple(k for k in KINDS if k != "generic")


def today_ist(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(IST).date()


def parse_date(raw: str | None, now: datetime | None = None) -> date | None:
    if not raw or raw == "today":
        return today_ist(now)
    try:
        d = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    if d < FIRST_DATE or d > today_ist(now):
        return None
    return d


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(d: date) -> tuple[str, str]:
    start = datetime(d.year, d.month, d.day, tzinfo=IST)
    return _iso(start), _iso(start + timedelta(days=1))


def _snap(row: Any) -> dict[str, Any]:
    try:
        metrics = json.loads(row["anomaly_metrics_json"] or "{}")
    except (TypeError, ValueError):
        metrics = {}
    metrics, _ = scrub_pii_in_obj(metrics if isinstance(metrics, dict) else {})
    return {
        "id": str(row["id"]),
        "mission_slug": str(row["mission_slug"] or ""),
        "methodology_code": str(row["methodology_code"] or ""),
        "title": str(row["title"] or ""),
        "entity_name": str(row["entity_name"] or ""),
        "jurisdiction": str(row["jurisdiction"] or ""),
        "fiscal_year": str(row["fiscal_year"] or ""),
        "monetary_exposure_inr": float(row["monetary_exposure_inr"] or 0.0),
        "anomaly_metrics": metrics,
        "rule_triggered": str(row["rule_triggered"] or ""),
        "status": str(row["status"] or ""),
        "confirm_count": int(row["confirm_count"] or 0),
        "required_quorum": int(row["required_quorum"] or 3),
        "source_url": str(row["source_url"] or ""),
        "verified_at": row["verified_at"],
        "created_at": row["created_at"],
    }


def _fingerprint(edition: dict[str, Any]) -> str:
    body = {k: v for k, v in edition.items() if k not in ("fingerprint", "generated_at")}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_edition(conn: Any, d: date, now: datetime | None = None) -> dict[str, Any]:
    start, end = window(d)
    q = conn.execute

    top = [
        _snap(r)
        for r in q(
            f"SELECT {_SNAP_COLS} FROM findings WHERE status IN ('verified', 'rti_filed') AND {_VISIBLE} "
            "AND COALESCE(verified_at, created_at) >= ? AND COALESCE(verified_at, created_at) < ? "
            "ORDER BY monetary_exposure_inr DESC, id ASC LIMIT 6",
            (start, end),
        ).fetchall()
    ]
    recent: list[dict[str, Any]] = []
    if not top:
        week = _iso(datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(days=7))
        recent = [
            _snap(r)
            for r in q(
                f"SELECT {_SNAP_COLS} FROM findings WHERE status IN ('verified', 'rti_filed') AND {_VISIBLE} "
                "AND COALESCE(verified_at, created_at) >= ? AND COALESCE(verified_at, created_at) < ? "
                "ORDER BY COALESCE(verified_at, created_at) DESC, id ASC LIMIT 3",
                (week, start),
            ).fetchall()
        ]
    cleared = [
        _snap(r)
        for r in q(
            f"SELECT {_SNAP_COLS} FROM findings WHERE status IN ('cleared', 'resolved_explained') AND {_VISIBLE} "
            "AND COALESCE(verified_at, created_at) >= ? AND COALESCE(verified_at, created_at) < ? "
            "ORDER BY COALESCE(verified_at, created_at) DESC, id ASC LIMIT 3",
            (start, end),
        ).fetchall()
    ]
    corrections = [
        _snap(r)
        for r in q(
            f"SELECT {_SNAP_COLS} FROM findings WHERE {_VISIBLE} AND id IN ("
            "SELECT finding_id FROM finding_corrections WHERE created_at >= ? AND created_at < ?) "
            "ORDER BY id ASC LIMIT 3",
            (start, end),
        ).fetchall()
    ]
    desk_counts: dict[str, int] = {}
    new_leads = 0
    for r in q(
        f"SELECT methodology_code, COUNT(*) AS c FROM findings WHERE {_VISIBLE} "
        "AND created_at >= ? AND created_at < ? GROUP BY methodology_code",
        (start, end),
    ).fetchall():
        c = int(r["c"] or 0)
        new_leads += c
        k = kind_for(r["methodology_code"])
        desk_counts[k] = desk_counts.get(k, 0) + c
    desk = [{"kind": k, "count": c} for k, c in sorted(desk_counts.items(), key=lambda kv: (-kv[1], kv[0]))][:6]

    totals = {"confirmed": 0, "cleared": 0, "review": 0, "checks": 0}
    for r in q(
        f"SELECT status, COUNT(*) AS c FROM findings WHERE {_VISIBLE} AND created_at < ? GROUP BY status", (end,)
    ).fetchall():
        st, c = str(r["status"] or ""), int(r["c"] or 0)
        if st in ("verified", "rti_filed"):
            totals["confirmed"] += c
        elif st in ("cleared", "resolved_explained"):
            totals["cleared"] += c
        elif st == "peer_review":
            totals["review"] += c
    row = q("SELECT COUNT(*) AS c FROM peer_verifications WHERE created_at < ?", (end,)).fetchone()
    totals["checks"] = int(row["c"] or 0) if row is not None else 0

    projects: list[dict[str, Any]] = []
    for m in q(
        "SELECT slug, title, methodology_code, total_tasks, completed_tasks FROM missions "
        "WHERE status != 'archived' ORDER BY priority ASC, slug ASC"
    ).fetchall():
        slug = str(m["slug"])
        c = {"flagged": 0, "verified": 0, "cleared": 0, "disputed": 0, "verified_sum": 0.0}
        for r in q(
            f"SELECT status, COUNT(*) AS c, SUM(monetary_exposure_inr) AS s FROM findings "
            f"WHERE mission_slug = ? AND {_VISIBLE} AND created_at < ? GROUP BY status",
            (slug, end),
        ).fetchall():
            st, n_ = str(r["status"] or ""), int(r["c"] or 0)
            c["flagged"] += n_
            if st in ("verified", "rti_filed"):
                c["verified"] += n_
                c["verified_sum"] += float(r["s"] or 0.0)
            elif st in ("cleared", "resolved_explained"):
                c["cleared"] += n_
            elif st in ("disputed", "retracted", "retracted_false_positive"):
                c["disputed"] += n_
        total, done = int(m["total_tasks"] or 0), int(m["completed_tasks"] or 0)
        if not total and not c["flagged"]:
            continue
        big = q(
            f"SELECT {_SNAP_COLS} FROM findings WHERE mission_slug = ? AND status IN ('verified', 'rti_filed') "
            f"AND {_VISIBLE} AND created_at < ? ORDER BY monetary_exposure_inr DESC, id ASC LIMIT 1",
            (slug, end),
        ).fetchone()
        projects.append({
            "slug": slug,
            "title": str(m["title"] or slug),
            "kind": kind_for(m["methodology_code"]),
            "total_tasks": total,
            "completed_tasks": min(done, total) if total else done,
            **c,
            "largest": _snap(big) if big is not None else None,
        })
    projects.sort(key=lambda p_: (-p_["verified"], -p_["flagged"], p_["slug"]))

    pool = top or recent
    number = max(pool, key=lambda s: s["monetary_exposure_inr"], default=None)
    if number is not None and number["monetary_exposure_inr"] <= 0:
        number = None

    n = d.toordinal()
    edition: dict[str, Any] = {
        "v": EDITION_VERSION,
        "date": d.isoformat(),
        "tz": "Asia/Kolkata",
        "window": [start, end],
        "generated_at": _iso(now or datetime.now(timezone.utc)),
        "stats": {
            "confirmed": len(top),
            "cleared": len(cleared),
            "new_leads": new_leads,
            "corrections": len(corrections),
        },
        "top": top,
        "recent": recent,
        "cleared": cleared,
        "corrections": corrections,
        "desk": desk,
        "totals": totals,
        "projects": projects,
        "number": number["id"] if number else None,
        "explainer": EXPLAINERS[n % len(EXPLAINERS)],
        "spotlight": SPOTLIGHT[(n // len(EXPLAINERS) + n) % len(SPOTLIGHT)],
    }
    edition["fingerprint"] = _fingerprint(edition)
    return edition


def lede_hash(edition: dict[str, Any]) -> str:
    ids = sorted(s["id"] for k in ("top", "recent", "cleared", "corrections") for s in edition.get(k) or [])
    stats = edition.get("stats") or {}
    raw = json.dumps([edition.get("date"), ids, stats.get("confirmed"), stats.get("cleared")], default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def get_edition(db: Any, d: date, now: datetime | None = None) -> dict[str, Any]:
    live = d >= today_ist(now)
    key = f"{CACHE_PREFIX}{d.isoformat()}"
    with db.read() as conn:
        if not live:
            row = conn.execute("SELECT payload_json FROM summary_cache WHERE cache_key = ?", (key,)).fetchone()
            if row is not None:
                try:
                    cached = json.loads(row["payload_json"])
                    if isinstance(cached, dict) and cached.get("v") == EDITION_VERSION:
                        return {**cached, "state": "final"}
                except (TypeError, ValueError):
                    pass
        edition = build_edition(conn, d, now)
    if not live and not getattr(db, "read_only", False):
        try:
            with db.transaction() as wconn:
                wconn.execute(
                    "INSERT INTO summary_cache (cache_key, version_stamp, payload_json, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO NOTHING",
                    (key, edition["fingerprint"], json.dumps(edition, default=str), edition["generated_at"]),
                )
        except Exception:
            pass
    return {**edition, "state": "live" if live else "final"}


def list_dates(db: Any, now: datetime | None = None) -> list[str]:
    today = today_ist(now)
    first = today
    with db.read() as conn:
        row = conn.execute(f"SELECT MIN(created_at) AS m FROM findings WHERE {_VISIBLE}").fetchone()
    if row is not None and row["m"]:
        try:
            first = datetime.fromisoformat(str(row["m"]).replace("Z", "+00:00")).astimezone(IST).date()
        except ValueError:
            first = today
    first = max(first, FIRST_DATE, today - timedelta(days=MAX_ARCHIVE - 1))
    days = (today - first).days
    return [(today - timedelta(days=i)).isoformat() for i in range(days + 1)]


def _story(s: dict[str, Any], lang: str, written: dict[str, Any] | None = None) -> dict[str, Any] | None:
    card = explain_finding(s, lang, (written or {}).get(f"finding:{s['id']}"))
    if card is None:
        return None
    return {
        "finding_id": s["id"],
        "head": card["head"],
        "what": card["what"],
        "why": card["why"],
        "ask": card["ask"],
        "status": card["status"],
        "project": card["project"],
        "kind": card["kind"],
        "source": card.get("source", "template"),
        "amount": fmt.money(s.get("monetary_exposure_inr"), lang),
        "source_url": s.get("source_url") or "",
    }


MAX_MINUTES = 5
_TRIMS = ((6, 3, True), (5, 3, True), (4, 2, True), (3, 1, True), (3, 1, False), (2, 1, False), (1, 0, False))


def render(
    edition: dict[str, Any],
    lang: str,
    base_url: str = SITE,
    written: dict[str, Any] | None = None,
    lede: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for top_n, cleared_n, all_projects in _TRIMS:
        trimmed = {
            **edition,
            "top": (edition.get("top") or [])[:top_n],
            "recent": (edition.get("recent") or [])[:top_n],
            "cleared": (edition.get("cleared") or [])[:cleared_n],
            "projects": [
                p for p in edition.get("projects") or [] if all_projects or p.get("flagged") or p.get("completed_tasks")
            ],
        }
        out = _render(trimmed, lang, base_url, written, lede, lede_hash(edition))
        if out["reading_minutes"] <= MAX_MINUTES:
            break
    return out


def _render(
    edition: dict[str, Any],
    lang: str,
    base_url: str,
    written: dict[str, Any] | None,
    lede: dict[str, Any] | None,
    full_hash: str = "",
) -> dict[str, Any]:
    p = pack(lang)
    news = section(lang, "news")
    stats = edition.get("stats") or {}

    def num(v: Any) -> str:
        return fmt.number(v, lang) or "0"

    busy = bool(stats.get("confirmed") or stats.get("cleared"))
    intro = (
        fill(news["intro_busy"], {"verified": num(stats.get("confirmed")), "cleared": num(stats.get("cleared")), "new": num(stats.get("new_leads"))})
        if busy
        else news["intro_quiet"]
    )
    sections: list[dict[str, Any]] = []

    def add_stories(sec_id: str, title_key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stories = [st for st in (_story(s, lang, written) for s in items) if st]
        if stories:
            sections.append({"id": sec_id, "title": news[title_key], "stories": stories})
        return stories

    site = base_url.rstrip("/")
    state_items = []
    for pr in edition.get("projects") or []:
        name = text(lang, "kinds", pr["kind"], "name") if pr.get("kind") != "generic" else pr.get("title", "")
        lines = []
        if pr.get("flagged"):
            lines.append(fill(news["state_counts"], {k: num(pr.get(k)) for k in ("flagged", "verified", "cleared", "disputed")}))
        if pr.get("total_tasks"):
            lines.append(fill(news["state_tasks"], {"done": num(pr.get("completed_tasks")), "total": num(pr.get("total_tasks"))}))
        example = _story(pr["largest"], lang, written) if pr.get("largest") else None
        if pr.get("verified_sum") and pr.get("verified", 0) > 1:
            amount = fmt.money(pr["verified_sum"], lang)
            if amount:
                lines.append(fill(news["state_sum"], {"amount": amount}))
        if example:
            lines.append(fill(news["state_example"], {"head": example["head"]}))
        elif pr.get("flagged") and not pr.get("verified"):
            lines.append(news["state_none"])
        state_items.append({
            "slug": pr["slug"],
            "name": name,
            "lines": lines,
            "finding_id": example["finding_id"] if example else None,
            "source_url": example["source_url"] if example else "",
            "project_url": f"{site}/projects/{pr['slug']}",
            "prompt": f"Fetch and follow {SITE}/join.txt and start auditing project {pr['slug']} now",
        })
    if state_items:
        sections.append({
            "id": "state",
            "title": news["sec_state"],
            "text": fill(news["state_intro"], {"date": str(edition.get("date"))}),
            "projects": state_items,
        })

    top = add_stories("top", "sec_top", edition.get("top") or [])
    if not top:
        top = add_stories("recent", "sec_recent", edition.get("recent") or [])

    number_id = edition.get("number")
    if number_id:
        hit = next((st for st in top if st["finding_id"] == number_id), None)
        if hit and hit["amount"]:
            sections.append({"id": "number", "title": news["sec_number"], "big": hit["amount"], "text": hit["head"], "finding_id": number_id})

    add_stories("cleared", "sec_cleared", edition.get("cleared") or [])

    corr = [st for st in (_story(s, lang, written) for s in edition.get("corrections") or []) if st]
    if corr:
        sections.append({
            "id": "corrections",
            "title": news["sec_corrections"],
            "lines": [{"text": fill(news["correction_line"], {"head": st["head"]}), "finding_id": st["finding_id"]} for st in corr],
        })

    desk = edition.get("desk") or []
    desk_lines = [
        {"text": fill(news["desk_line"], {"project": text(lang, "kinds", d["kind"], "name"), "count": num(d["count"])})}
        for d in desk
        if d.get("count")
    ]
    sections.append({
        "id": "desk",
        "title": news["sec_desk"],
        "lines": desk_lines or [{"text": news["desk_empty"]}],
        "note": news["desk_note"] if desk_lines else "",
    })

    totals = edition.get("totals") or {}
    if any(totals.values()):
        sections.append({
            "id": "totals",
            "title": news["sec_totals"],
            "text": fill(news["totals_line"], {k: num(totals.get(k)) for k in ("confirmed", "cleared", "review", "checks")}),
        })

    ex_key = str(edition.get("explainer") or EXPLAINERS[0])
    ex = section(lang, "explainers", ex_key)
    sections.append({"id": "explainer", "title": news["sec_explainer"], "heading": ex.get("title", ""), "text": ex.get("body", "")})

    sp_kind = str(edition.get("spotlight") or SPOTLIGHT[0])
    sp = section(lang, "kinds", sp_kind)
    sections.append({"id": "spotlight", "title": news["sec_spotlight"], "heading": sp.get("name", ""), "text": f"{sp.get('about', '')} {sp.get('why', '')}".strip()})

    sections.append({"id": "act", "title": news["sec_act"], "text": news["act_body"]})
    sections.append({
        "id": "cta",
        "title": news["sec_cta"],
        "text": news["cta_body"],
        "prompt": f"Fetch and follow {SITE}/join.txt and start a civic-audit agent now",
    })

    parts: list[str] = [news["title"], intro]
    for sec in sections:
        parts.append(sec["title"])
        for st in sec.get("stories", []):
            parts.extend([st["head"], st["what"], st["why"], st["status"]])
        for ln in sec.get("lines", []):
            parts.append(ln["text"])
        for pr in sec.get("projects", []):
            parts.append(pr["name"])
            parts.extend(pr["lines"])
        for k in ("big", "heading", "text", "note"):
            if sec.get(k):
                parts.append(str(sec[k]))
    parts.append(news["footer"])
    readable = " ".join(parts)
    minutes = fmt.reading_minutes(readable + " " + " ".join(st["ask"] for sec in sections for st in sec.get("stories", [])), lang)

    d = str(edition.get("date"))
    url = f"{base_url.rstrip('/')}/news/{d}" + ("" if lang == "en" else f"?lang={lang}")
    heads = [st["head"] for sec in sections for st in sec.get("stories", [])][:5]
    share = "\n".join([f"{news['title']} · {d}", intro, *[f"• {h}" for h in heads], url])

    lede_text, lede_model = "", ""
    if lede and lede.get("hash") == (full_hash or lede_hash(edition)):
        entry = (lede.get("langs") or {}).get(lang) or {}
        lede_text, lede_model = str(entry.get("lede") or ""), str(lede.get("model") or "")
    if lede_text:
        readable = f"{lede_text} {readable}"
        share = share.replace(intro, lede_text, 1)

    ui = labels(lang)
    return {
        "lede": lede_text,
        "lede_model": lede_model,
        "date": d,
        "state": edition.get("state", "final"),
        "lang": p["code"],
        "dir": p.get("dir", "ltr"),
        "bcp47": p.get("bcp47", p["code"]),
        "title": news["title"],
        "tagline": news["tagline"],
        "edition_label": fill(ui["edition_of"], {"date": d}),
        "intro": intro,
        "reading_minutes": minutes,
        "reading_label": fill(ui["min_read"], {"n": num(minutes)}),
        "sections": sections,
        "footer": news["footer"],
        "fingerprint": edition.get("fingerprint", ""),
        "fingerprint_label": fill(news["fingerprint"], {"hash": edition.get("fingerprint", "-")}),
        "stats": stats,
        "speak": readable,
        "share_text": share,
        "url": url,
        "labels": ui,
    }


def render_markdown(r: dict[str, Any]) -> str:
    out = [f"# {r['title']} · {r['date']}", "", f"_{r['tagline']}_ · {r['reading_label']}", ""]
    if r.get("lede"):
        out += [r["lede"], ""]
    out += [r["intro"], ""]
    for sec in r["sections"]:
        out += [f"## {sec['title']}", ""]
        if sec.get("big"):
            out += [f"**{sec['big']}** · {sec.get('text', '')}", ""]
        for st in sec.get("stories", []):
            out += [f"### {st['head']}", "", st["what"], "", f"{st['why']}", "", f"> {st['ask']}", "", f"_{st['status']}_"]
            src = f" · [{r['labels'].get('source', 'Source')}]({st['source_url']})" if st["source_url"].startswith("http") else ""
            out += ["", f"[{r['labels'].get('open_finding', 'Open')}]({SITE}/findings/{st['finding_id']}){src}", ""]
        if sec.get("projects"):
            out += [sec.get("text", ""), ""]
            for pr in sec["projects"]:
                links = [f"[{pr['name']}]({pr['project_url']})"]
                if pr.get("finding_id"):
                    links.append(f"[{r['labels'].get('open_finding', 'Open')}]({SITE}/findings/{pr['finding_id']})")
                out.append(f"- **{pr['name']}.** " + " ".join(pr["lines"]) + " " + ", ".join(links))
            out.append("")
        for ln in sec.get("lines", []):
            out.append(f"- {ln['text']}")
        if sec.get("lines"):
            out.append("")
        if sec.get("heading"):
            out += [f"**{sec['heading']}**", ""]
        if sec.get("text") and not sec.get("big") and not sec.get("projects"):
            out += [sec["text"], ""]
        if sec.get("prompt"):
            out += ["```text", sec["prompt"], "```", ""]
        if sec.get("note"):
            out += [f"_{sec['note']}_", ""]
    out += ["---", "", r["footer"], "", f"`{r['fingerprint_label']}`", ""]
    return "\n".join(out)
