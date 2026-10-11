import logging
import re
import socketserver
import threading
from datetime import timedelta

import pytest

from backend import database as dbm
from backend import mailer, verification
from tests.test_api import REQ
from tests.test_delivery import env, spec2  # noqa: F401  (fixtures/helpers shared with the delivery tests)

LINK = re.compile(r"/verify#([A-Za-z0-9_-]+)")


@pytest.fixture
def outbox(monkeypatch):
    """Mail 'configured' (log mode) with delivery captured instead of written to the log."""
    monkeypatch.setenv("MAIL_MODE", "log")
    box = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: box.append((to, subject, text, html)) or "sent")
    return box


def token_from(msg) -> str:
    return LINK.search(msg[2]).group(1)


def submit(client, **over):
    return client.post("/api/requests", json={**REQ, **over})


# ------------------------------------------------------------------ the email
def test_submission_sends_one_fixed_template_email_without_requester_text(client, outbox):
    r = submit(client, company_name="Totally Legit Bank <b>urgent</b>", business_problem="Click http://evil.example now!!!",
               tasks="send your password", github_username="evil-user").json()
    assert len(outbox) == 1
    to, subject, text, html = outbox[0]
    assert to == REQ["contact_email"] and subject == "Confirm your email for Nexara AI" and r["request_id"] in text
    for typed in ("Totally Legit", "evil.example", "password", "evil-user", "urgent"):
        assert typed not in text and typed not in html                      # nothing the requester typed reaches a third party
    assert LINK.search(text) and "/verify#" in html                        # fragment link: not sent to servers, absent from access logs
    token = token_from(outbox[0])
    assert token not in str(r) and r["request"]["email_verified"] is False
    with dbm.db() as c:                                                      # only a hash is stored
        assert token not in "".join(str(list(x)) for x in c.execute("SELECT * FROM email_verifications").fetchall())


def test_response_is_identical_whether_or_not_mail_is_configured(client, monkeypatch, outbox):
    a = submit(client).json()
    monkeypatch.setenv("MAIL_MODE", "off")
    b = submit(client, contact_email="other@acme.example", github_username="other-dev").json()
    assert set(a) == set(b) and a["message"] == b["message"]                # no oracle for mail config or address existence


def test_not_configured_mail_records_why_for_staff_and_does_not_fail(client, monkeypatch):
    monkeypatch.setenv("MAIL_MODE", "off")
    r = submit(client).json()
    assert r["request"]["email_verified"] is False
    kinds = {e["kind"]: e for e in dbm.list_events(r["request"]["customer_id"])}
    assert "verification_not_sent" in kinds and "SMTP_HOST" in kinds["verification_not_sent"]["detail"]["reason"]


# ------------------------------------------------------------------ confirming
def test_confirm_flow_single_use_and_uniform_errors(client, outbox):
    rid = submit(client).json()["request_id"]
    t = token_from(outbox[0])
    r = client.post("/api/verify", json={"token": t})
    assert r.status_code == 200 and r.json() == {"ok": True}
    got = client.get(f"/api/requests/{rid}").json()
    assert got["email_verified"] is True and got["email_verified_via"] == "link"
    assert "email_verified" in {e["kind"] for e in dbm.list_events(got["customer_id"])}
    again = client.post("/api/verify", json={"token": t})
    garbage = client.post("/api/verify", json={"token": "x" * 43})
    assert again.status_code == garbage.status_code == 400 and again.json() == garbage.json()      # same answer for every failure
    assert client.post("/api/verify", json={"token": "short"}).status_code == 422
    assert client.post("/api/verify", json={}).status_code == 422


def test_expired_token_is_rejected(client, outbox):
    rid = submit(client).json()["request_id"]
    with dbm.db() as c:
        c.execute("UPDATE email_verifications SET expires_at=?", ("2000-01-01T00:00:00.000000+00:00",))
    assert client.post("/api/verify", json={"token": token_from(outbox[0])}).status_code == 400
    assert client.get(f"/api/requests/{rid}").json()["email_verified"] is False


def test_using_a_token_invalidates_the_requests_other_tokens(client, outbox, monkeypatch):
    monkeypatch.setattr(verification, "COOLDOWN", timedelta(0))
    rid = submit(client).json()["request_id"]
    assert client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True}).status_code == 200
    t1, t2 = token_from(outbox[0]), token_from(outbox[1])
    assert t1 != t2
    assert client.post("/api/verify", json={"token": t2}).status_code == 200
    assert client.post("/api/verify", json={"token": t1}).status_code == 400


def test_verify_endpoint_is_rate_limited(client):
    codes = [client.post("/api/verify", json={"token": "y" * 43}).status_code for _ in range(22)]
    assert codes[:20] == [400] * 20 and codes[20:] == [429, 429]


def test_verify_page_and_script_are_served(client):
    page = client.get("/verify")
    assert page.status_code == 200 and "Confirm my email" in page.text and "verify.js" in page.text
    js = client.get("/js/verify.js").text
    assert "location.hash" in js and "location.search" not in js           # token comes from the fragment, never the query string


# ------------------------------------------------------------------ the gate on delivery
def test_delivery_is_blocked_until_the_email_is_verified(env, outbox):
    _, aid, cid = env.mk(verified=False)
    p = env.client.get(f"/api/agents/{aid}/delivery-preview").json()
    assert p["email_verified"] is False and "not verified" in p["blocked"][0]
    r = env.deliver(aid)
    assert r.status_code == 409 and "not verified" in r.json()["detail"] and "Nothing was created" in r.json()["detail"]
    assert env.fake.repos == {} and dbm.get_repo_for_customer(cid) is None
    assert env.client.post("/api/verify", json={"token": token_from(outbox[-1])}).status_code == 200
    assert env.client.get(f"/api/agents/{aid}/delivery-preview").json()["blocked"] == []
    assert env.deliver(aid).status_code == 201


def test_attacker_using_a_victims_identity_cannot_deliver(env, outbox, monkeypatch):
    monkeypatch.setattr(verification, "COOLDOWN", timedelta(0))      # (normally the 5-minute cooldown would even suppress the email)
    _, a1, c1 = env.mk(company="Acme Corp", gh="victim-dev", email="ceo@acme.example")
    assert env.deliver(a1).status_code == 201
    outbox.clear()
    # company name, GitHub user and email all copied from the victim: auto-links to the victim's customer...
    _, a2, c2 = env.mk(company="Acme Corp", gh="victim-dev", email="ceo@acme.example", spec=spec2("Evil Agent"), verified=False)
    assert c2 == c1
    assert [m[0] for m in outbox] == ["ceo@acme.example"]               # ...but the confirmation mail goes to the VICTIM's mailbox
    before = env.fake.files("nexara-acme-corp")
    r = env.deliver(a2, existing_repo_ack=True)
    assert r.status_code == 409 and "not verified" in r.json()["detail"]
    assert env.fake.files("nexara-acme-corp") == before                  # nothing reaches the victim's repo
    # the victim ignoring the unexpected email changes nothing; only their click would verify
    assert env.client.get(f"/api/requests/{dbm.list_requests()[0]['id']}").json()["email_verified"] is False


def test_the_other_gates_still_apply_after_verification(env, outbox):
    _, a1, c1 = env.mk(company="Acme Corp", gh="victim-dev", email="ceo@acme.example")
    env.deliver(a1)
    rid2, a2, _ = env.mk(company="Acme Corp", gh="colleague", email="eng@acme.example", spec=spec2(), verified=False)
    env.client.post(f"/api/requests/{rid2}/attach-customer", json={"customer_id": c1, "confirm": True})
    env.client.post("/api/verify", json={"token": token_from(outbox[-1])})        # the colleague proves THEIR mailbox
    p = env.client.get(f"/api/agents/{a2}/delivery-preview").json()
    assert p["blocked"] == [] and p["invite"]["will_invite"] is False and p["invite"]["blocked_reason"]
    assert env.deliver(a2, invite=True, existing_repo_ack=True).status_code == 409   # verified mailbox != verified GitHub identity
    assert env.deliver(a2, invite=False, existing_repo_ack=True).status_code == 201


# ------------------------------------------------------------------ staff actions
def test_manual_verification_needs_confirm_note_and_is_recorded(env):
    rid, aid, cid = env.mk(verified=False)
    assert env.client.post(f"/api/requests/{rid}/verify-manually", json={"note": "confirmed by phone"}).status_code == 400
    assert env.client.post(f"/api/requests/{rid}/verify-manually", json={"confirm": True, "note": "ok"}).status_code == 422
    assert env.client.post("/api/requests/REQ-NOPE/verify-manually", json={"confirm": True, "note": "confirmed by phone"}).status_code == 404
    r = env.client.post(f"/api/requests/{rid}/verify-manually", json={"confirm": True, "note": "confirmed by phone on 11 Oct"})
    assert r.status_code == 200 and r.json()["email_verified_via"] == "staff" and r.json()["email_verified_note"] == "confirmed by phone on 11 Oct"
    ev = [e for e in dbm.list_events(cid) if e["kind"] == "email_verified"][0]
    assert ev["detail"]["via"] == "staff" and "phone" in ev["detail"]["note"]
    assert env.deliver(aid).status_code == 201
    assert env.client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True}).status_code == 409   # already verified


def test_resend_needs_confirm_and_reports_mail_problems(client, monkeypatch, outbox):
    rid = submit(client).json()["request_id"]
    assert client.post(f"/api/requests/{rid}/resend-verification", json={}).status_code == 400
    assert client.post("/api/requests/REQ-NOPE/resend-verification", json={"confirm": True}).status_code == 404
    monkeypatch.setattr(verification, "COOLDOWN", timedelta(0))
    monkeypatch.setattr(mailer, "send", lambda *a, **k: (_ for _ in ()).throw(mailer.MailError("Could not send email (SMTPException).")))
    assert client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True}).status_code == 502
    monkeypatch.setenv("MAIL_MODE", "off")
    r = client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True})
    assert r.status_code == 503 and "not configured" in r.json()["detail"].lower()


def test_cooldown_and_daily_cap_limit_email_bombing(client, monkeypatch, outbox):
    rid = submit(client).json()["request_id"]                              # email 1
    r = client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True})
    assert r.status_code == 429 and "few minutes" in r.json()["detail"]    # cooldown
    monkeypatch.setattr(verification, "COOLDOWN", timedelta(0))
    assert client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True}).status_code == 200    # email 2
    assert client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True}).status_code == 200    # email 3
    r = client.post(f"/api/requests/{rid}/resend-verification", json={"confirm": True})
    assert r.status_code == 429 and "3 confirmation emails" in r.json()["detail"] and len(outbox) == 3           # daily cap


def test_one_address_cannot_be_used_to_flood_a_mailbox_via_new_requests(client, monkeypatch, outbox):
    monkeypatch.setattr(verification, "COOLDOWN", timedelta(0))
    for i in range(6):
        assert submit(client, company_name=f"Spam Co {i}").status_code == 201           # public answer never changes
    assert len(outbox) == 3                                                              # but only DAILY_CAP mails were ever sent
    assert len([e for c in dbm.list_customers() for e in dbm.list_events(c["id"]) if e["kind"] == "verification_not_sent"]) == 3


def test_mail_status_and_new_endpoints_require_login(client, outbox):
    assert client.get("/api/mail/status").json()["configured"] is True
    client.headers.pop("X-Factory-Token")
    assert client.get("/api/mail/status").status_code == 401
    assert client.post("/api/requests/REQ-X/resend-verification", json={"confirm": True}).status_code == 401
    assert client.post("/api/requests/REQ-X/verify-manually", json={"confirm": True, "note": "xxxxx"}).status_code == 401
    assert client.post("/api/verify", json={"token": "z" * 43}).status_code == 400          # the confirm endpoint is public by design


# ------------------------------------------------------------------ the mailer itself (real SMTP exchange against a local sink)
class _Sink(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 sink ESMTP")
        data, buf = False, []
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if data:
                if line == ".":
                    data = False
                    self.server.messages.append("\n".join(buf))
                    buf = []
                    w("250 queued")
                else:
                    buf.append(line[1:] if line.startswith("..") else line)
                continue
            up = line.upper()
            if up == "DATA":
                data = True
                w("354 go ahead")
            elif up == "QUIT":
                w("221 bye")
                return
            elif up.startswith("AUTH"):
                w("535 no")
            else:
                w("250 OK")


@pytest.fixture
def smtp_sink(monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Sink)
    srv.daemon_threads, srv.messages = True, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("MAIL_MODE", "smtp")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(srv.server_address[1]))
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("SMTP_FROM", "Nexara AI <noreply@nexara.example>")
    monkeypatch.delenv("SMTP_USER", raising=False)
    yield srv
    srv.shutdown()


def test_real_smtp_exchange_delivers_a_multipart_message(client, smtp_sink):
    rid = submit(client).json()["request_id"]
    verification.send_safely  # (the background task already sent one through the sink)
    assert len(smtp_sink.messages) == 1
    m = smtp_sink.messages[0]
    assert "Subject: Confirm your email for Nexara AI" in m and "To: ops@acme.example" in m and "noreply@nexara.example" in m
    assert "multipart/alternative" in m and "text/plain" in m and "text/html" in m and rid in m and "/verify#" in m


def test_mailer_refuses_header_injection_and_reports_connection_errors(monkeypatch, smtp_sink):
    with pytest.raises(mailer.MailError):
        mailer.send("victim@x.example\nBcc: attacker@evil.example", "s", "t")
    with pytest.raises(mailer.MailError):
        mailer.send("a@b.example", "subject\r\nBcc: attacker@evil.example", "t")
    assert not smtp_sink.messages
    smtp_sink.shutdown()
    monkeypatch.setenv("SMTP_PORT", "1")
    with pytest.raises(mailer.MailError) as e:
        mailer.send("a@b.example", "s", "t")
    assert "OSError" in str(e.value) or "Connection" in str(e.value)


def test_starttls_ssl_and_login_paths_and_the_password_never_leaks(monkeypatch, caplog):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            calls.append(("connect", type(self).__name__, host, port, context is not None))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            calls.append(("starttls", context is not None))

        def login(self, user, password):
            calls.append(("login", user))
            raise mailer.smtplib.SMTPAuthenticationError(535, b"bad credentials s3cret-pw")

        def send_message(self, msg):
            calls.append(("send", msg["To"]))

    class FakeSSL(FakeSMTP):
        pass
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSSL)
    for k, v in {"MAIL_MODE": "smtp", "SMTP_HOST": "smtp.example", "SMTP_FROM": "n@x.example", "SMTP_USER": "u", "SMTP_PASSWORD": "s3cret-pw"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    with caplog.at_level(logging.DEBUG), pytest.raises(mailer.MailError) as e:
        mailer.send("a@b.example", "s", "t")
    assert calls[:3] == [("connect", "FakeSMTP", "smtp.example", 587, False), ("starttls", True), ("login", "u")]   # TLS before credentials
    assert "s3cret-pw" not in str(e.value) and "s3cret-pw" not in caplog.text and "SMTPAuthenticationError" in str(e.value)
    calls.clear()
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    with pytest.raises(mailer.MailError):
        mailer.send("a@b.example", "s", "t")
    assert calls[0] == ("connect", "FakeSSL", "smtp.example", 465, True)


def test_mail_modes_and_status(monkeypatch, caplog):
    monkeypatch.delenv("MAIL_MODE", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.mode() == "off" and mailer.status()["configured"] is False
    with pytest.raises(mailer.MailNotConfigured):
        mailer.send("a@b.example", "s", "t")
    monkeypatch.setenv("SMTP_HOST", "h")
    assert mailer.mode() == "smtp" and mailer.status()["configured"] is False        # needs SMTP_FROM too
    monkeypatch.setenv("MAIL_MODE", "log")
    with caplog.at_level(logging.WARNING, logger="nexara.mail"):
        assert mailer.send("a@b.example", "Hi", "body text") == "logged"
    assert "body text" in caplog.text and "not sent" in caplog.text
