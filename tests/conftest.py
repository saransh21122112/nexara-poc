import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXARA_DB", str(tmp_path / "t.db"))
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    pg = os.environ.get("TEST_DATABASE_URL")  # run the whole suite against Postgres: TEST_DATABASE_URL=postgresql://...
    if pg:
        monkeypatch.setenv("DATABASE_URL", pg)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    from backend import providers
    providers._current = None
    from backend import main as _m
    _m._buckets.clear()
    from backend.main import app
    if pg:
        from backend import database
        database.init_db()
        with database.db() as cx:
            cx.execute("TRUNCATE email_verifications, repo_events, repo_agents, repos, customers, eval_runs, runs, agents, requests CASCADE")
    with TestClient(app) as c:
        tok = c.post("/api/factory/login", json={"password": "nexara-demo"}).json()["token"]
        c.headers["X-Factory-Token"] = tok
        yield c
