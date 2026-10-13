"""Spec 007 behaviours 6-8 in a real browser: no overflow, hidden page scrollbar, accessible, works on phones and without JavaScript."""
import pytest

from .conftest import NOANIM, PASSWORD, open_page

pytestmark = pytest.mark.ui
PUBLIC = ["/", "/about", "/what-we-do", "/how-we-work", "/security", "/faq", "/contact", "/privacy", "/terms", "/request", "/track", "/verify"]
WIDTHS = [320, 390, 768, 1440]

AUDIT = """() => {
  const de = document.documentElement, cw = de.clientWidth;
  const off = [...document.querySelectorAll('body *')].filter(e => { const b = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return b.width && cs.position !== 'fixed' && cs.visibility !== 'hidden' && (b.right > cw + 1 || b.left < -1) && !e.closest('.sheet,.sr,.hp,svg,.graph,aside,.pipe-panel,pre,.dv,.log,table:not(.stack)'); }).map(e => e.tagName + '.' + e.className);
  const small = [...document.querySelectorAll('a,button,input,select,textarea,summary')].filter(e => { const b = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (!b.width || cs.visibility === 'hidden' || e.closest('.sheet,.hp,.sr,#app') || e.type === 'hidden') return false;
    const inline = e.tagName === 'A' && cs.display === 'inline' && e.closest('p,li,dd'); return !inline && (b.height < 32 || (b.width < 32 && !e.matches('input[type=checkbox],input[type=radio]'))); }).map(e => e.tagName + ':' + (e.textContent || '').trim().slice(0, 20));
  return { gutter: innerWidth - cw, over: de.scrollWidth - cw, off, small, h1: document.querySelectorAll('h1').length };
}"""


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", PUBLIC)
def test_public_page_fits_the_screen_has_no_page_scrollbar_and_big_enough_targets(page, site, path, width):
    open_page(page, site, path, width)
    r = page.evaluate(AUDIT)
    assert r["over"] <= 0, f"horizontal overflow {r}"
    assert r["off"] == [], f"elements outside the viewport: {r['off']}"
    assert r["gutter"] == 0, "the page scrollbar must be hidden"
    assert r["small"] == [], f"tap targets too small: {r['small']}"
    assert r["h1"] == 1
    assert page.errors == [], page.errors


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("path", PUBLIC)
def test_public_page_has_no_accessibility_violations(page, site, path, width):
    from axe_playwright_python.sync_playwright import Axe
    open_page(page, site, path, width)
    res = Axe().run(page, options={"runOnly": ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"]})
    assert res.violations_count == 0, res.generate_report()


def test_mobile_menu_opens_traps_focus_and_closes_on_escape(page, site):
    open_page(page, site, "/about", 390)
    assert page.get_attribute("#menu", "aria-expanded") == "false" and not page.is_visible("#sheet a")
    page.focus("#menu"); page.keyboard.press("Enter")
    assert page.get_attribute("#menu", "aria-expanded") == "true" and page.is_visible("#sheet a")
    assert page.evaluate("document.getElementById('sheet').contains(document.activeElement)")
    assert page.evaluate("document.body.classList.contains('lock')")
    page.keyboard.press("Escape")
    assert page.get_attribute("#menu", "aria-expanded") == "false" and not page.evaluate("document.body.classList.contains('lock')")
    page.click("#menu"); page.set_viewport_size({"width": 1200, "height": 800}); page.wait_for_timeout(150)
    assert not page.evaluate("document.body.classList.contains('lock')")


def test_public_pages_work_without_javascript(browser, site):
    ctx = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 800})
    pg = ctx.new_page(); pg.goto(site + "/", wait_until="load")
    assert pg.evaluate("[...document.querySelectorAll('.rv')].filter(e => getComputedStyle(e).opacity === '0').length") == 0
    assert not pg.is_visible("#menu") and pg.locator(".links a:not(.btn)").first.is_visible()
    ctx.close()


def test_reduced_motion_shows_everything(browser, site):
    ctx = browser.new_context(reduced_motion="reduce", viewport={"width": 640, "height": 800})
    pg = ctx.new_page(); pg.goto(site + "/", wait_until="load"); pg.wait_for_timeout(300)
    assert pg.evaluate("[...document.querySelectorAll('.rv')].filter(e => getComputedStyle(e).opacity === '0').length") == 0
    assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    ctx.close()


def test_contact_form_validates_then_confirms(page, site):
    open_page(page, site, "/contact", 390)
    page.click("#csend")
    assert {e.text_content() for e in page.query_selector_all(".err") if e.text_content()} == {"Please enter your name", "Enter a valid email address", "Please write at least 10 characters"}
    page.fill("#c_name", "Test Visitor"); page.fill("#c_email", "visitor@example.com"); page.fill("#c_msg", "A message that is long enough to send.")
    page.wait_for_timeout(300); page.click("#csend")
    page.wait_for_selector(".check", timeout=8000)
    assert "Message sent" in page.inner_text("main .box h2")


def test_request_form_submits_with_optional_examples_left_empty(page, site):
    open_page(page, site, "/request?agent=Tool", 390)
    assert page.input_value("#agent_type") == "Tool"
    for k, v in {"company_name": "UI Test Co", "industry": "Testing", "contact_email": "ui@example.com", "business_problem": "Reports take hours every week.",
                 "tasks": "Summarise the weekly numbers.", "existing_tools": "Excel", "required_output": "A one page summary.", "github_username": "ui-test-dev"}.items():
        page.fill("#" + k, v)
    page.click("#go")
    page.wait_for_selector(".success", timeout=8000)
    rid = page.inner_text(".success .id")
    assert rid.startswith("REQ-") and page.get_attribute(".success a.btn", "href") == f"/track?id={rid}"
    open_page(page, site, f"/track?id={rid}")
    page.wait_for_selector("#res:not([hidden])", timeout=5000)
    assert page.locator(".ts").count() == 6


def test_unknown_page_is_a_styled_404(page, site):
    r = page.goto(site + "/definitely-missing")
    assert r.status == 404 and "Page not found" in page.inner_text("h1")


@pytest.mark.parametrize("width", [390, 1280])
def test_factory_views_fit_and_are_accessible_after_login(page, site, width):
    from axe_playwright_python.sync_playwright import Axe
    open_page(page, site, "/factory", width)
    page.fill("#pw", PASSWORD); page.press("#pw", "Enter")
    page.wait_for_selector("#app:not([hidden])")
    assert page.evaluate("innerWidth - document.documentElement.clientWidth") == 0, "the dashboard page scrollbar must be hidden"
    for view in ["overview", "requests", "customers", "live", "builder", "playground", "messages"]:
        page.click(f"button.nav[data-v={view}]"); page.wait_for_timeout(700)
        r = page.evaluate(AUDIT)
        assert r["over"] <= 0 and r["off"] == [], f"{view}: {r}"
        res = Axe().run(page, options={"runOnly": ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"]})
        assert res.violations_count == 0, f"{view}: {res.generate_report()}"
    assert page.errors == [], page.errors


def test_wrong_factory_password_is_refused_without_a_token(page, site):
    open_page(page, site, "/factory")
    page.fill("#pw", "wrong"); page.press("#pw", "Enter")
    page.wait_for_selector("#lerr:not([hidden])")
    assert page.is_hidden("#app") and page.evaluate("sessionStorage.getItem('ft')") is None


@pytest.mark.parametrize("width", [390, 1280])
def test_artifact_panel_shows_files_code_checks_and_stays_accessible(page, site, width):
    import json
    import sqlite3

    from axe_playwright_python.sync_playwright import Axe

    from backend.models import AgentSpec
    from tests.artifacts import golden
    from .conftest import UI_DB
    rid = page.request.post(site + "/api/requests", data={"company_name": "Panel Co", "industry": "Testing", "contact_email": "panel@example.com", "agent_type": "Tool",
                                                           "business_problem": "Spreadsheets need checking.", "tasks": "Check CSV files.", "existing_tools": "Excel",
                                                           "required_output": "A list of problems.", "github_username": "panel-dev", "claude_status": "have_claude_account"}).json()["request_id"]
    con = sqlite3.connect(UI_DB["path"])
    con.execute("INSERT INTO agents (id, request_id, created_at, updated_at, provider, model, spec) VALUES (?,?,?,?,?,?,?)",
                (f"AGT-PANEL{width}", rid, "2026-01-01", "2026-01-01", "anthropic", "fake", json.dumps(AgentSpec(**golden.tool_only()).model_dump())))
    con.commit(); con.close()
    open_page(page, site, "/factory", width)
    page.fill("#pw", PASSWORD); page.press("#pw", "Enter")
    page.wait_for_selector("#app:not([hidden])")
    page.click("button.nav[data-v=builder]"); page.wait_for_selector("#ag")
    page.select_option("#ag", f"AGT-PANEL{width}")
    page.wait_for_selector("#artifact .ft")
    assert "not verified" in page.inner_text("#artifact h3")
    assert "NEXARA_RUN_GENERATED_CODE" in page.inner_text("#artifact .warn")
    assert "def handle(args):" in page.inner_text("#vcode") and page.inner_text("#vname").endswith(".py")
    page.click("#artifact .ft:has-text('lib/nx_cli.py')")
    assert "vetted Nexara building block" in page.inner_text("#vcode")
    page.click("#rchk"); page.wait_for_selector("#artifact details")
    assert page.evaluate("innerWidth - document.documentElement.clientWidth") == 0
    r = page.evaluate(AUDIT)
    assert r["over"] <= 0 and r["off"] == [], r
    res = Axe().run(page, options={"runOnly": ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"]})
    assert res.violations_count == 0, res.generate_report()
    assert page.errors == [], page.errors
