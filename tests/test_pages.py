"""Spec 007: server-rendered public pages, SEO files, legal config, content rule, CSP."""
import re

import pytest

PUBLIC = ["/", "/about", "/what-we-do", "/how-we-work", "/security", "/faq", "/contact", "/privacy", "/terms", "/request"]
PRIVATE = ["/track", "/verify", "/factory"]
# Public text says what customers get, never how Nexara works inside (spec 007, behaviour 4).
DENY = ["langchain", "langgraph", "fastapi", "uvicorn", "pydantic", "anthropic", "openai", "sonnet", "opus", "haiku", "claude_subscription",
        "git data", "blob", "spec_hash", "sqlite", "postgres", "render.com", "onrender", "prompt", "pytest", "docker", "build_events",
        "repo_changes", "force-push", "force push", "sha-256", "sha256", "json", "endpoint", "api key", "webhook", "oauth"]


def text_of(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages_render_with_shared_chrome_and_meta(client, path):
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    h = r.text
    assert '<html lang="en"' in h and re.search(r"<title>[^<]{5,}</title>", h)
    assert re.search(r'<meta name="description" content="[^"]{20,}"', h) and '<link rel="canonical"' in h
    assert "<nav" in h and "<footer" in h and 'href="/privacy"' in h and 'href="/terms"' in h and 'href="/contact"' in h
    assert len(re.findall(r"<h1[ >]", h)) == 1 and 'content="noindex' not in h
    assert "{{" not in h and "None" not in text_of(h)


@pytest.mark.parametrize("path", ["/track", "/verify", "/factory"])
def test_private_pages_are_noindex(client, path):
    assert 'name="robots" content="noindex' in client.get(path).text


def test_sitemap_and_robots(client):
    s = client.get("/sitemap.xml")
    assert s.status_code == 200 and "xml" in s.headers["content-type"]
    for p in PUBLIC[1:]:
        assert f"<loc>http://testserver{p}</loc>" in s.text
    for p in PRIVATE:
        assert p not in s.text
    r = client.get("/robots.txt").text
    for p in ("/factory", "/track", "/verify", "/api"):
        assert f"Disallow: {p}" in r
    assert "Sitemap: http://testserver/sitemap.xml" in r


def test_unknown_page_is_a_styled_404_and_api_stays_json(client):
    r = client.get("/definitely-not-here")
    assert r.status_code == 404 and "Page not found" in r.text and "<nav" in r.text
    j = client.get("/api/definitely-not-here")
    assert j.status_code == 404 and j.headers["content-type"].startswith("application/json")


def test_legal_values_come_from_env_and_unset_values_render_nothing(client, monkeypatch):
    for k in ("CONTACT_EMAIL", "LEGAL_NAME", "LEGAL_ADDRESS", "POLICY_EFFECTIVE", "POLICY_REVIEWED"):
        monkeypatch.delenv(k, raising=False)
    bare = client.get("/privacy").text
    assert "pending legal review" in bare and "mailto:" not in bare and "None" not in bare
    monkeypatch.setenv("LEGAL_NAME", "Nexara Labs <b>Pvt</b>")
    monkeypatch.setenv("CONTACT_EMAIL", "hello@nexara.example")
    monkeypatch.setenv("LEGAL_ADDRESS", "1 Example Street")
    monkeypatch.setenv("POLICY_EFFECTIVE", "1 November 2026")
    monkeypatch.setenv("POLICY_REVIEWED", "true")
    full = client.get("/privacy").text
    assert "Nexara Labs &lt;b&gt;Pvt&lt;/b&gt;" in full and "<b>Pvt</b>" not in full                # escaped
    assert "mailto:hello@nexara.example" in full and "1 Example Street" in full and "1 November 2026" in full
    assert "pending legal review" not in full
    assert "hello@nexara.example" in client.get("/contact").text and "Nexara Labs" in client.get("/terms").text


def test_public_pages_do_not_leak_internals(client):
    for path in PUBLIC + ["/sitemap.xml", "/robots.txt"]:
        t = text_of(client.get(path).text)
        for bad in DENY:
            assert bad not in t, f"{path} reveals '{bad}'"


@pytest.mark.parametrize("path", PUBLIC + PRIVATE)
def test_csp_and_no_inline_scripts(client, path):
    r = client.get(path)
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
        assert part in csp
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", r.text), "inline script found"
    assert not re.search(r"\son[a-z]+=", r.text), "inline event handler found"


def test_security_headers_survive(client):
    r = client.get("/about")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_contact_page_has_the_form_and_trap_fields(client):
    h = client.get("/contact").text
    assert '<form id="cf"' in h and 'name="website"' in h and 'name="message"' in h and 'name="topic"' in h
    assert "/js/contact.js" in h


def test_fonts_are_self_hosted(client):
    h = client.get("/").text
    assert "fonts.googleapis.com" not in h and "fonts.gstatic.com" not in h
