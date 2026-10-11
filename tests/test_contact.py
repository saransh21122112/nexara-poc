"""Spec 007 behaviour 5: the public contact form."""
import pytest

from backend import database as dbm, mailer

OK = dict(name="Jo Reader", email="jo@example.com", topic="General", message="Hello, I would like to talk about a skill for my team.")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("CONTACT_MIN_SECONDS", "0")


def ticket(client):
    r = client.get("/api/contact/ticket")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.json()["ticket"]


def send(client, **over):
    body = {**OK, "ticket": ticket(client), "website": "", **over}
    return client.post("/api/contact", json=body)


def test_valid_message_is_stored_and_listed_for_staff_only(client):
    r = send(client)
    assert r.status_code == 201 and r.json() == {"ok": True}
    rows = client.get("/api/messages").json()
    assert len(rows) == 1 and rows[0]["email"] == "jo@example.com" and rows[0]["read"] is False and rows[0]["topic"] == "General"
    client.headers.pop("X-Factory-Token")
    assert client.get("/api/messages").status_code == 401 and client.post("/api/messages/MSG-X/read").status_code == 401


def test_mark_read_and_unknown_id(client):
    send(client)
    mid = client.get("/api/messages").json()[0]["id"]
    assert client.post(f"/api/messages/{mid}/read").status_code == 200
    assert client.get("/api/messages").json()[0]["read"] is True
    assert client.post("/api/messages/MSG-NOPE/read").status_code == 404


def test_validation(client):
    for over in ({"email": "nope"}, {"message": "short"}, {"message": "x" * 4001}, {"name": ""}, {"name": "n" * 121}, {"topic": "Spam"}):
        assert send(client, **over).status_code == 422, over
    assert client.get("/api/messages").json() == []


def test_honeypot_looks_like_success_but_stores_nothing(client):
    r = send(client, website="http://spam.example")
    assert r.status_code == 201 and r.json() == {"ok": True} and client.get("/api/messages").json() == []


def test_too_fast_submission_is_dropped_quietly_and_bad_ticket_is_refused_visibly(client, monkeypatch):
    monkeypatch.setenv("CONTACT_MIN_SECONDS", "30")
    r = send(client)
    assert r.status_code == 201 and client.get("/api/messages").json() == []                    # a bot: no signal
    monkeypatch.setenv("CONTACT_MIN_SECONDS", "0")
    r = client.post("/api/contact", json={**OK, "ticket": "garbage", "website": ""})
    assert r.status_code == 400 and "reload" in r.json()["detail"].lower()                       # a human: told what to do


def test_per_ip_limit_and_a_hard_daily_cap(client, monkeypatch):
    monkeypatch.setenv("CONTACT_PER_HOUR_PER_IP", "2")
    assert [send(client).status_code for _ in range(3)] == [201, 201, 429]
    from backend import main
    main._buckets.clear()
    monkeypatch.setenv("CONTACT_PER_HOUR_PER_IP", "50")
    monkeypatch.setenv("CONTACT_HARD_PER_DAY", "3")
    main._contact_day[:] = [0, 0]
    assert [send(client).status_code for _ in range(4)][-1] == 429


def test_a_flood_never_blocks_real_messages_it_only_stops_operator_emails(client, monkeypatch):
    from backend import main
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append(1) or "smtp")
    monkeypatch.setattr(mailer, "mode", lambda: "smtp")
    monkeypatch.setenv("CONTACT_EMAIL", "ops@nexara.example")
    monkeypatch.setenv("CONTACT_PER_DAY", "2")
    monkeypatch.setenv("CONTACT_PER_HOUR_PER_IP", "50")
    main._contact_day[:] = [0, 0]
    codes = [send(client).status_code for _ in range(5)]
    assert codes == [201] * 5 and len(client.get("/api/messages").json()) == 5 and len(sent) == 2


def test_ticket_key_is_not_derived_from_the_staff_password(client, monkeypatch):
    from backend import main
    t = client.get("/api/contact/ticket").json()["ticket"]
    ts = t.split(".")[0]
    import hashlib, hmac
    for guess in ("nexara-demo", "nexara-contact:nexara-demo", "hunter2"):
        assert not hmac.compare_digest(t, f"{ts}." + hmac.new(f"nexara-contact:{guess}".encode(), ts.encode(), hashlib.sha256).hexdigest()[:32])
    monkeypatch.setenv("CONTACT_SECRET", "x" * 40)
    assert main._ticket_age(main._sign(int(__import__("time").time()))) is not None


def test_html_is_stored_as_plain_data_never_interpreted(client):
    send(client, message="<img src=x onerror=alert(1)> please call me back about a tool")
    assert "<img src=x onerror=alert(1)>" in client.get("/api/messages").json()[0]["message"]


def test_operator_is_emailed_only_when_mail_is_configured(client, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)) or "smtp")
    monkeypatch.setenv("CONTACT_EMAIL", "ops@nexara.example")
    monkeypatch.setattr(mailer, "mode", lambda: "smtp")
    send(client)
    assert len(sent) == 1 and sent[0][0] == "ops@nexara.example" and "jo@example.com" in sent[0][2]
    monkeypatch.setattr(mailer, "mode", lambda: "none")
    send(client)
    assert len(sent) == 1 and len(client.get("/api/messages").json()) == 2                       # still stored


def test_a_mail_failure_never_fails_the_visitor(client, monkeypatch):
    def boom(*a, **k):
        raise mailer.MailError("down")
    monkeypatch.setattr(mailer, "send", boom)
    monkeypatch.setattr(mailer, "mode", lambda: "smtp")
    monkeypatch.setenv("CONTACT_EMAIL", "ops@nexara.example")
    assert send(client).status_code == 201 and len(client.get("/api/messages").json()) == 1
