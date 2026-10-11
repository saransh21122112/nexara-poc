"""Email verification for requests. The public form proves nothing about who is typing, so nothing is delivered to a customer
until the requester's own mailbox confirmed (or a person verified them another way and said how)."""
import os
from datetime import datetime, timedelta, timezone

from . import database as dbm
from . import mailer

COOLDOWN = timedelta(minutes=5)
DAILY_CAP = 3
TTL_HOURS = 48


class VerificationLimit(Exception):
    pass


def public_url() -> str:
    return (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")


def link(token: str) -> str:
    return f"{public_url()}/verify#{token}"      # fragment: never sent to the server, so it stays out of access logs and referrers


def _check_limits(email: str):
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="microseconds")
    times = dbm.verification_times(email, since)
    if times and datetime.fromisoformat(times[-1]) > datetime.now(timezone.utc) - COOLDOWN:
        raise VerificationLimit("A confirmation email was sent to this address a few minutes ago. Wait a little before sending another.")
    if len(times) >= DAILY_CAP:
        raise VerificationLimit(f"Already sent {DAILY_CAP} confirmation emails to this address in 24 hours. Use manual verification or try tomorrow.")


def send_verification(request: dict) -> str:
    """Creates a token and emails the link. The message is a fixed template: it contains no text the requester typed, so the form
    cannot be used to put attacker-chosen words in front of a third party. Raises VerificationLimit / MailNotConfigured / MailError."""
    if not mailer.status()["configured"]:
        raise mailer.MailNotConfigured(mailer.status()["issue"])
    _check_limits(request["contact_email"])
    token = dbm.create_verification(request["id"], request["contact_email"], TTL_HOURS)
    url = link(token)
    text = (f"Someone used this email address to ask Nexara AI to build an AI agent (reference {request['id']}).\n\n"
            f"If that was you, confirm your address here:\n{url}\n\n"
            f"The link works once and expires in {TTL_HOURS} hours. If it wasn't you, ignore this email: nothing happens and "
            f"nothing is created.\n\n- Nexara AI\n")
    html = (f"<p>Someone used this email address to ask Nexara AI to build an AI agent (reference {request['id']}).</p>"
            f"<p>If that was you, <a href=\"{url}\">confirm your address</a>.</p>"
            f"<p>The link works once and expires in {TTL_HOURS} hours. If it wasn't you, ignore this email: nothing happens and nothing is created.</p>")
    return mailer.send(request["contact_email"], "Confirm your email for Nexara AI", text, html)


def send_safely(request: dict) -> None:
    """For background use right after a public submission: never raises; records the outcome for staff (no tokens, no secrets)."""
    cid = request.get("customer_id")
    try:
        mode = send_verification(request)
        if cid:
            dbm.add_event(cid, "verification_sent", {"request": request["id"], "mode": mode})
    except Exception as e:      # noqa: BLE001
        if cid:
            dbm.add_event(cid, "verification_not_sent", {"request": request["id"], "reason": str(e)[:200]})


def confirm(token: str) -> bool:
    rid = dbm.consume_verification(token) if token and 20 <= len(token) <= 200 else None
    if not rid:
        return False
    dbm.mark_email_verified(rid, "link")
    return True
