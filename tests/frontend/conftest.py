"""Browser (UI) tests: the real app on a free port with a throwaway database, driven by Chromium via Playwright.
Skipped automatically when Playwright or its browser is not installed (pip install -r requirements.txt; playwright install chromium)."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright")
ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "ui-test-password"
UI_DB = {}      # path of the throwaway database, so tests can seed rows the UI has no form for


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    UI_DB["path"] = str(tmp_path_factory.mktemp("ui") / "ui.db")
    env = {**os.environ, "NEXARA_DB": UI_DB["path"], "FACTORY_PASSWORD": PASSWORD, "AI_PROVIDER": "anthropic",
           "CONTACT_MIN_SECONDS": "0", "REQUIRE_EMAIL_VERIFICATION": "false", "LEGAL_NAME": "Nexara AI",
           "POLICY_REVIEWED": "true"}
    for k in ("DATABASE_URL", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1); break
        except Exception:
            time.sleep(0.25)
    else:
        proc.kill(); pytest.fail("app did not start")
    yield base
    proc.terminate(); proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:     # browser binary missing
            pytest.skip(f"Chromium not installed: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


NOANIM = "*,*:before,*:after{animation:none!important;transition:none!important}.js .rv{opacity:1!important;transform:none!important}"


def open_page(page, base, path, width=None):
    if width:
        page.set_viewport_size({"width": width, "height": 800})
    page.goto(base + path, wait_until="load")
    page.add_style_tag(content=NOANIM)
