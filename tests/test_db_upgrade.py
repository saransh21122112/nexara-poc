import sqlite3

from backend import database as dbm

OLD_REPOS = """CREATE TABLE repos (id TEXT PRIMARY KEY, customer_id TEXT UNIQUE, owner TEXT, name TEXT, url TEXT, created_at TEXT, state TEXT,
  invite_status TEXT, invited_user TEXT, invitation_id TEXT, head_sha TEXT, synced_at TEXT, last_error TEXT, files TEXT)"""


def test_existing_repos_table_gains_layout_and_legacy_without_losing_rows(tmp_path, monkeypatch):
    """A database created by the previous release (no layout/legacy columns) must upgrade in place and treat old repos as layout 2."""
    db = tmp_path / "old.db"
    monkeypatch.setenv("NEXARA_DB", str(db))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    con = sqlite3.connect(db)
    con.execute(OLD_REPOS)
    con.execute("CREATE TABLE customers (id TEXT PRIMARY KEY, slug TEXT UNIQUE, company_name TEXT, contact_email TEXT, github_username TEXT, claude_status TEXT, os TEXT, created_at TEXT, onboarding TEXT)")
    con.execute("INSERT INTO customers VALUES ('CUS-1','budget-desk','budget_desk','a@b.co','u',NULL,NULL,'2026-01-01','{}')")
    con.execute("INSERT INTO repos VALUES ('REPO-1','CUS-1','owner','nexara-budget-desk','https://x','2026-01-01','ready','accepted','u',NULL,'abc',NULL,NULL,'{\"README.md\": \"sha\"}')")
    con.commit()
    con.close()
    dbm.init_db()
    dbm.init_db()                                         # idempotent: running twice must not fail
    repo = dbm.get_repo_for_customer("CUS-1")
    assert repo["name"] == "nexara-budget-desk" and repo["files"] == {"README.md": "sha"} and repo["invite_status"] == "accepted"
    assert repo["layout"] == 2 and repo["legacy"] == []   # old rows are the one-plugin-per-agent layout
    dbm.update_repo(repo["id"], layout=3)
    dbm.set_repo_legacy(repo["id"], ["old-a", "old-a", "old-b"])
    again = dbm.get_repo_for_customer("CUS-1")
    assert again["layout"] == 3 and again["legacy"] == ["old-a", "old-b"]
