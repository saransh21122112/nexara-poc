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


def test_legal_wording_comes_from_env_and_unset_values_render_nothing(client, monkeypatch):
    for k in ("LEGAL_NAME", "POLICY_EFFECTIVE", "POLICY_REVIEWED"):
        monkeypatch.delenv(k, raising=False)
    bare = client.get("/privacy").text
    assert "pending legal review" in bare and "None" not in bare and "{{" not in bare and "Effective:" not in bare
    monkeypatch.setenv("LEGAL_NAME", "Nexara Labs <b>Pvt</b>")
    monkeypatch.setenv("POLICY_EFFECTIVE", "1 November 2026")
    monkeypatch.setenv("POLICY_REVIEWED", "true")
    full = client.get("/privacy").text
    assert "Nexara Labs &lt;b&gt;Pvt&lt;/b&gt;" in full and "<b>Pvt</b>" not in full                # escaped
    assert "1 November 2026" in full and "pending legal review" not in full
    assert "Nexara Labs" in client.get("/terms").text


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


# ---- spec 008: the owner's details are written directly in the HTML
EMAIL, PHONE_TEL, LINKEDIN, GITHUB = "saranshgupta2112@gmail.com", "tel:+917291812344", "https://www.linkedin.com/in/saransh-Gupta21", "https://github.com/saransh21122112"
OWNER_ENV = ("CONTACT_EMAIL", "CONTACT_PHONE", "OWNER_NAME", "OWNER_ROLE", "OWNER_LOCATION", "OWNER_LINKEDIN", "OWNER_GITHUB", "LEGAL_ADDRESS")


def test_contact_page_shows_owner_card(client):
    c = client.get("/contact").text
    assert 'class="owner-card"' in c and "Saransh Gupta" in c and "Faridabad, Haryana, India" in c
    for needle in (f"mailto:{EMAIL}", PHONE_TEL, "+91 72918 12344", f'href="{LINKEDIN}"', f'href="{GITHUB}"'):
        assert needle in c, needle
    assert c.count('rel="noopener noreferrer"') >= 2


def test_about_introduces_the_owner_without_internals(client):
    t = text_of(client.get("/about").text)
    assert "who is behind nexara" in t and "saransh gupta" in t and "ai engineer" in t and "faridabad" in t
    for bad in DENY + ["hrs", "bedrock", "langchain", "qdrant", "booking.com", "foundry", "arcface", "lambda"]:
        assert bad not in t, bad


def test_footer_shows_email_and_linkedin_but_phone_only_on_contact(client):
    for path in PUBLIC:
        h = client.get(path).text
        foot = h[h.index("<footer"):]
        assert f"mailto:{EMAIL}" in foot and "linkedin.com/in/saransh-Gupta21" in foot, path
        if path != "/contact":
            assert "72918" not in h and "tel:" not in h, path


def test_legal_pages_name_the_operator(client):
    for p in ("/privacy", "/terms"):
        h = client.get(p).text
        t = text_of(h)
        assert "operated by saransh gupta" in t and "faridabad, haryana, india" in t and f"mailto:{EMAIL}" in h, p


def test_contact_details_are_in_the_html_and_ignore_environment_settings(client, monkeypatch):
    before = {p: client.get(p).text for p in ("/contact", "/about", "/privacy", "/terms", "/")}
    for k in OWNER_ENV:
        monkeypatch.setenv(k, "<script>x</script>@evil.example")
    for p, h in before.items():
        assert client.get(p).text == h, f"{p} changed when env settings were set"
        assert "evil.example" not in client.get(p).text
    for k in OWNER_ENV:
        monkeypatch.setenv(k, "")
    assert f"mailto:{EMAIL}" in client.get("/contact").text and "72918" in client.get("/contact").text
    from backend import pages
    assert not hasattr(pages, "OWNER_DEFAULTS")


def test_notify_email_matches_the_html(client):
    from backend import pages
    assert pages.NOTIFY_EMAIL == EMAIL and f"mailto:{pages.NOTIFY_EMAIL}" in client.get("/contact").text
