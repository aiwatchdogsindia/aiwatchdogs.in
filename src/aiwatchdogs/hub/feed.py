"""Public Atom feeds: every public change of state, newest first.

Feed readers keep copies we cannot recall, so leads still in peer review are left out:
only verified, cleared, corrected or retracted findings and neutral project activity appear.
Quarantined findings never appear.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

ATOM = "http://www.w3.org/2005/Atom"
FEED_LIMIT = 50
ID_BASE = "https://aiwatchdogs.in"  # IDs must not change with the host serving the feed
EPOCH = "1970-01-01T00:00:00Z"
_XML_ILLEGAL = re.compile("[^\t\n\r -퟿-�\U00010000-\U0010ffff]")
_PUBLISHED = "('verified', 'rti_filed', 'cleared', 'resolved_explained')"
_VISIBLE = "COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined' AND COALESCE(f.is_demo_sample, 0) = 0"
_CORRECTION_TERMS = {
    "retracted": ("Retracted", "finding_retracted"),
    "retracted_false_positive": ("Retracted", "finding_retracted"),
    "under_review": ("Editorial review", "finding_under_review"),
    "disputed": ("Disputed", "finding_disputed"),
}


def feed_entries(conn: Any, *, slug: str | None = None, limit: int = FEED_LIMIT) -> list[dict[str, str]]:
    """Collect feed entries, optionally for one project."""
    entries: list[dict[str, str]] = []

    def scoped(column: str) -> tuple[str, tuple[Any, ...]]:
        return (f" AND {column} = ?", (slug, limit)) if slug else ("", (limit,))

    def add(key: str, when: Any, term: str, label: str, title: Any, path: str, summary: str) -> None:
        entries.append(
            {
                "key": key,
                "updated": str(when or EPOCH),
                "term": term,
                "title": f"{label}: {title}",
                "path": path,
                "summary": summary,
            }
        )

    def finding_path(fid: Any) -> str:
        return f"/findings?finding={quote(str(fid), safe='')}"

    def project_path(value: Any) -> str:
        return f"/projects/{quote(str(value), safe='')}"

    where, params = scoped("f.mission_slug")
    for r in conn.execute(
        f"SELECT f.id, f.title, f.status, f.jurisdiction, f.methodology_code, "
        f"COALESCE(f.verified_at, f.created_at) AS at FROM findings f "
        f"WHERE f.status IN {_PUBLISHED} AND {_VISIBLE}{where} ORDER BY at DESC LIMIT ?",
        params,
    ).fetchall():
        verified = r["status"] in ("verified", "rti_filed")
        add(
            f"finding-{r['id']}-{'verified' if verified else 'cleared'}",
            r["at"],
            "finding_verified" if verified else "finding_cleared",
            "Verified" if verified else "Cleared",
            r["title"],
            finding_path(r["id"]),
            f"{r['methodology_code']} · {r['jurisdiction']} · "
            + ("Reproduced by three independent agents. A statistical lead, not a legal determination." if verified else "Explained or within limits."),
        )

    for r in conn.execute(
        f"SELECT c.id, c.finding_id, c.new_status, c.summary, c.created_at, f.title FROM finding_corrections c "
        f"JOIN findings f ON f.id = c.finding_id WHERE {_VISIBLE}{where} ORDER BY c.created_at DESC LIMIT ?",
        params,
    ).fetchall():
        label, term = _CORRECTION_TERMS.get(str(r["new_status"] or ""), ("Corrected", "finding_corrected"))
        add(f"correction-{r['id']}", r["created_at"], term, label, r["title"], finding_path(r["finding_id"]), str(r["summary"]))

    for r in conn.execute(
        f"SELECT d.id, d.finding_id, d.public_authority, d.created_at, f.title FROM rti_drafts d "
        f"JOIN findings f ON f.id = d.finding_id WHERE d.status != 'quarantined' "
        f"AND f.status IN ('verified', 'rti_filed') AND {_VISIBLE}{where} ORDER BY d.created_at DESC LIMIT ?",
        params,
    ).fetchall():
        add(
            f"rti-{r['id']}", r["created_at"], "rti_drafted", "RTI drafted", r["title"],
            finding_path(r["finding_id"]), f"Draft for {r['public_authority']}. A citizen reviews and files it.",
        )

    where, params = scoped("t.mission_slug")
    for r in conn.execute(
        "SELECT t.id, t.title, t.mission_slug, t.completed_at, t.claimed_by_handle, m.title AS project "
        "FROM tasks t JOIN missions m ON m.id = t.mission_id "
        "WHERE t.status = 'completed' AND t.task_type != 'peer_verify' AND t.completed_at IS NOT NULL "
        f"AND m.is_community_project = 1 AND m.status != 'archived'{where} ORDER BY t.completed_at DESC LIMIT ?",
        params,
    ).fetchall():
        by = f" by @{r['claimed_by_handle']}" if r["claimed_by_handle"] else ""
        add(
            f"task-{r['id']}-completed", r["completed_at"], "task_completed", "Task finished", r["title"],
            project_path(r["mission_slug"]), f"{r['project']}{by}",
        )

    where, params = scoped("m.slug")
    for r in conn.execute(
        "SELECT m.slug, m.title, m.created_at, m.jurisdiction FROM missions m "
        f"WHERE m.is_community_project = 1 AND m.status != 'archived'{where} ORDER BY m.created_at DESC LIMIT ?",
        params,
    ).fetchall():
        add(
            f"project-{r['slug']}", r["created_at"], "project_listed", "Project added", r["title"],
            project_path(r["slug"]), str(r["jurisdiction"]),
        )

    entries.sort(key=lambda e: (e["updated"], e["key"]), reverse=True)
    return entries[:limit]


_GRAVEYARD_STATUSES = (
    "('retracted_false_positive', 'retracted', 'disputed', "
    "'under_review', 'under_editorial_review', 'corrected')"
)
_GRAVEYARD_LABELS = {
    "retracted_false_positive": ("False positive", "graveyard_false_positive"),
    "retracted": ("Retracted", "graveyard_retracted"),
    "disputed": ("Peer dispute", "graveyard_disputed"),
    "under_review": ("Withdrawn for review", "graveyard_withdrawn"),
    "under_editorial_review": ("Withdrawn for review", "graveyard_withdrawn"),
    "corrected": ("Withdrawn for review", "graveyard_withdrawn"),
}


def graveyard_feed_entries(conn: Any, *, limit: int = FEED_LIMIT) -> list[dict[str, str]]:
    """Collect non-quarantined graveyard entries (false positives, retractions, disputes, withdrawals)."""
    rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT f.id, f.title, f.status, f.jurisdiction, f.methodology_code, f.created_at,
                   (SELECT c.created_at FROM finding_corrections c WHERE c.finding_id = f.id
                    ORDER BY c.created_at DESC LIMIT 1) AS correction_at,
                   (SELECT c.summary FROM finding_corrections c WHERE c.finding_id = f.id
                    ORDER BY c.created_at DESC LIMIT 1) AS correction_summary,
                   (SELECT MAX(v.created_at) FROM peer_verifications v WHERE v.finding_id = f.id
                    AND v.verdict != 'confirm') AS dispute_at
            FROM findings f
            WHERE f.status IN {_GRAVEYARD_STATUSES} AND {_VISIBLE}
        ) AS sub
        ORDER BY COALESCE(sub.correction_at, sub.dispute_at, sub.created_at) DESC, sub.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    entries: list[dict[str, str]] = []
    for r in rows:
        fid = str(r["id"])
        st = str(r["status"] or "").lower()
        label, term = _GRAVEYARD_LABELS.get(st, ("Retired", "graveyard_retired"))
        when = str(r["correction_at"] or r["dispute_at"] or r["created_at"] or EPOCH)
        detail = str(r["correction_summary"] or "").strip()
        summary = f"{r['methodology_code']} · {r['jurisdiction']}" + (f" · {detail}" if detail else "")
        entries.append(
            {
                "key": f"graveyard-{fid}",
                "updated": when,
                "term": term,
                "title": f"{label}: {r['title']}",
                "path": f"/findings?finding={quote(fid, safe='')}",
                "summary": summary,
            }
        )
    entries.sort(key=lambda e: (e["updated"], e["key"]), reverse=True)
    return entries[:limit]


def render_atom(entries: list[dict[str, str]], *, base_url: str, path: str, title: str, page: str) -> bytes:
    """Render entries as an Atom 1.0 document."""
    ET.register_namespace("", ATOM)

    def node(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
        el = ET.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
        if text is not None:
            el.text = _XML_ILLEGAL.sub("", text)
        return el

    feed = ET.Element(f"{{{ATOM}}}feed")
    node(feed, "title", title)
    node(feed, "id", f"{ID_BASE}{path}")
    node(feed, "link", rel="self", type="application/atom+xml", href=f"{base_url}{path}")
    node(feed, "link", rel="alternate", type="text/html", href=f"{base_url}{page}")
    node(feed, "updated", entries[0]["updated"] if entries else EPOCH)
    node(node(feed, "author"), "name", "AI Watchdogs India")
    for e in entries:
        entry = node(feed, "entry")
        node(entry, "id", f"{ID_BASE}/feed#{quote(e['key'], safe='-')}")
        node(entry, "title", e["title"])
        node(entry, "updated", e["updated"])
        node(entry, "link", href=f"{base_url}{e['path']}")
        node(entry, "category", term=e["term"])
        node(entry, "summary", e["summary"])
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)
