"""Outgoing email. MAIL_MODE=smtp (default when SMTP_HOST is set) | log (writes the message, including links, to the server log: local
development only) | off. SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY=starttls|ssl|none.
Works with any provider's SMTP (Gmail app password, Amazon SES, Resend, Mailgun ...). The password is never logged."""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("nexara.mail")


class MailNotConfigured(Exception):
    pass


class MailError(Exception):
    pass


def mode() -> str:
    m = os.environ.get("MAIL_MODE", "").lower()
    if m in ("smtp", "log", "off"):
        return m
    return "smtp" if os.environ.get("SMTP_HOST") else "off"


def status() -> dict:
    m = mode()
    ok = m == "log" or (m == "smtp" and bool(os.environ.get("SMTP_HOST")) and bool(os.environ.get("SMTP_FROM")))
    return {"mode": m, "configured": ok, "from": os.environ.get("SMTP_FROM") if m == "smtp" else None,
            "issue": None if ok else "Set SMTP_HOST and SMTP_FROM (and SMTP_USER/SMTP_PASSWORD), or MAIL_MODE=log for local development."}


def send(to: str, subject: str, text: str, html: str | None = None) -> str:
    """Returns 'sent' or 'logged'. Raises MailNotConfigured / MailError (messages never contain credentials)."""
    st = status()
    if not st["configured"]:
        raise MailNotConfigured(st["issue"])
    if mode() == "log":
        log.warning("MAIL (log mode, not sent) to=%s subject=%s\n%s", to, subject, text)
        return "logged"
    msg = EmailMessage()
    try:                                      # EmailMessage rejects header injection (newlines in addresses/subjects)
        msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    except ValueError:
        raise MailError("Invalid email address or subject.") from None
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    host, security = os.environ["SMTP_HOST"], os.environ.get("SMTP_SECURITY", "starttls").lower()
    port = int(os.environ.get("SMTP_PORT") or (465 if security == "ssl" else 587))
    try:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
        with smtp:
            if security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Could not send email ({type(e).__name__}).") from None
    return "sent"
