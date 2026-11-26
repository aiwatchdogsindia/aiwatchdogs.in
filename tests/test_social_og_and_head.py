"""Regression tests for social share Open Graph / Twitter metadata, CORP headers, and HEAD routes."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app, create_degraded_app

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
PUBLIC_DIR = WEB_DIR / "public"


def _png_dimensions(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a valid PNG"
    width, height = struct.unpack(">II", raw[16:24])
    return width, height


def test_social_og_assets_and_index_html_metadata() -> None:
    logo_orig = PUBLIC_DIR / "logo-512.png"
    og_logo = PUBLIC_DIR / "og-logo-v2.png"
    og_card = PUBLIC_DIR / "og-card-v2.png"

    # Existing legacy/public brand files stay intact
    assert logo_orig.is_file()
    assert (PUBLIC_DIR / "logo.svg").is_file()
    assert (PUBLIC_DIR / "favicon.svg").is_file()

    # Versioned v2 social assets exist with exact expected dimensions
    assert og_logo.is_file()
    assert og_logo.read_bytes() == logo_orig.read_bytes()
    assert _png_dimensions(og_logo) == (512, 512)

    assert og_card.is_file()
    assert _png_dimensions(og_card) == (1200, 630)
    assert og_card.stat().st_size > 10_000

    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    # Mobile safe-area viewport
    assert 'content="width=device-width, initial-scale=1.0, viewport-fit=cover"' in html

    # Icons
    assert 'href="/favicon.svg?v=2"' in html
    assert 'rel="icon" type="image/png" sizes="512x512" href="/og-logo-v2.png"' in html
    assert 'rel="apple-touch-icon" href="/og-logo-v2.png"' in html

    # Open Graph & Twitter card point to versioned 1200x630 card (no stale /logo-512.png)
    assert "https://aiwatchdogs.in/logo-512.png" not in html
    assert '<meta property="og:image" content="https://aiwatchdogs.in/og-card-v2.png" />' in html
    assert '<meta property="og:image:secure_url" content="https://aiwatchdogs.in/og-card-v2.png" />' in html
    assert '<meta property="og:image:type" content="image/png" />' in html
    assert '<meta property="og:image:width" content="1200" />' in html
    assert '<meta property="og:image:height" content="630" />' in html
    assert 'property="og:image:alt"' in html
    assert '<meta name="twitter:card" content="summary_large_image" />' in html
    assert '<meta name="twitter:image" content="https://aiwatchdogs.in/og-card-v2.png" />' in html
    assert 'name="twitter:image:alt"' in html

    # JSON-LD Organization logo uses versioned square PNG
    ld_match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    assert ld_match is not None
    ld_data = json.loads(ld_match.group(1))
    assert ld_data["logo"] == "https://aiwatchdogs.in/og-logo-v2.png"


def test_vercel_json_corp_headers_allow_cross_origin_social_images() -> None:
    cfg = json.loads((REPO_ROOT / "vercel.json").read_text(encoding="utf-8"))
    headers_rules = cfg["headers"]
    assert headers_rules[0]["source"] == "/(.*)"

    # Root catch-all still sends same-origin by default
    root_corp = [h["value"] for h in headers_rules[0]["headers"] if h["key"] == "Cross-Origin-Resource-Policy"]
    assert root_corp == ["same-origin"]

    # Later rule(s) override CORP to cross-origin for public OG/logo/icon assets
    override_rules = [
        rule
        for rule in headers_rules[1:]
        if any(
            h["key"] == "Cross-Origin-Resource-Policy" and h["value"] == "cross-origin"
            for h in rule["headers"]
        )
    ]
    assert len(override_rules) >= 1

    def _matches_override(path: str) -> bool:
        return any(re.fullmatch(rule["source"], path) is not None for rule in override_rules)

    for public_asset in (
        "/og-card-v2.png",
        "/og-logo-v2.png",
        "/logo-512.png",
        "/logo.svg",
        "/logo-dark.svg",
        "/logo-lockup.svg",
        "/favicon.svg",
        "/apple-touch-icon.png",
        "/agents/claude.svg",
    ):
        assert _matches_override(public_asset), f"{public_asset} must match cross-origin CORP rule in vercel.json"

    for non_asset in ("/", "/index.html", "/api/head", "/missions", "/join.sh", "/llms.txt"):
        assert not _matches_override(non_asset), f"{non_asset} must NOT match cross-origin CORP rule"


def test_head_requests_and_corp_headers_on_spa_static_and_discovery_routes(tmp_path: Path) -> None:
    dist_dir = tmp_path / "web_dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "agents").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<!doctype html><html><body>AI Watchdogs</body></html>", encoding="utf-8")
    (dist_dir / "assets" / "app.js").write_text("console.log('ok');", encoding="utf-8")
    (dist_dir / "og-card-v2.png").write_bytes((PUBLIC_DIR / "og-card-v2.png").read_bytes())
    (dist_dir / "og-logo-v2.png").write_bytes((PUBLIC_DIR / "og-logo-v2.png").read_bytes())
    (dist_dir / "logo-512.png").write_bytes((PUBLIC_DIR / "logo-512.png").read_bytes())
    (dist_dir / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (dist_dir / "agents" / "claude.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")

    application = create_app(db_path=tmp_path / "head.db", seed=False, web_dist=dist_dir)
    with TestClient(application) as client:
        # Twitterbot / social crawler HEAD on / and SPA routes MUST return 200 (never 405)
        for spa_path in ("/", "/index.html", "/missions", "/projects", "/findings", "/live", "/graveyard"):
            resp = client.head(spa_path, headers={"User-Agent": "Twitterbot/1.0"})
            assert resp.status_code == 200, f"HEAD {spa_path} returned {resp.status_code}"
            assert resp.content == b""
            assert "text/html" in resp.headers.get("content-type", "")
            assert int(resp.headers.get("content-length", "0")) > 0
            assert resp.headers.get("cross-origin-resource-policy") == "same-origin"

        # HEAD with Accept: text/markdown on / returns 200 with markdown content-type and empty body
        md_head = client.head("/", headers={"Accept": "text/markdown"})
        assert md_head.status_code == 200
        assert md_head.content == b""
        assert "text/markdown" in md_head.headers.get("content-type", "")

        # Public image/icon assets support HEAD & GET and emit Cross-Origin-Resource-Policy: cross-origin
        for img_path in ("/og-card-v2.png", "/og-logo-v2.png", "/logo-512.png", "/favicon.svg", "/agents/claude.svg"):
            head_resp = client.head(img_path, headers={"User-Agent": "Twitterbot/1.0"})
            assert head_resp.status_code == 200, f"HEAD {img_path} returned {head_resp.status_code}"
            assert head_resp.content == b""
            assert head_resp.headers.get("cross-origin-resource-policy") == "cross-origin"

            get_resp = client.get(img_path)
            assert get_resp.status_code == 200
            assert get_resp.headers.get("cross-origin-resource-policy") == "cross-origin"

        # Built JS assets & public discovery/health/feed/markdown routes also support HEAD cleanly
        for extra_path in (
            "/assets/app.js",
            "/health",
            "/robots.txt",
            "/sitemap.xml",
            "/llms.txt",
            "/feed.atom",
            "/index.md",
            "/join.txt",
        ):
            r = client.head(extra_path)
            assert r.status_code == 200, f"HEAD {extra_path} returned {r.status_code}"
            assert r.content == b""

        # Unknown /api/* routes still return 404 on HEAD (with empty body)
        missing_api = client.head("/api/does-not-exist")
        assert missing_api.status_code == 404
        assert missing_api.content == b""

        # Non-asset routes retain strict same-origin CORP
        assert client.get("/api/head").headers.get("cross-origin-resource-policy") == "same-origin"
    application.state.db.close()

    # Degraded app /health also supports HEAD (503, empty body, not 405)
    degraded = create_degraded_app(reason="db unavailable")
    with TestClient(degraded) as dc:
        dh = dc.head("/health")
        assert dh.status_code == 503
        assert dh.content == b""
