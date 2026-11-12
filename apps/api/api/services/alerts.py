"""Alert fan-out: Slack, Discord and SMTP, with an always-on local audit row.

Sends are best-effort and recorded. A missing webhook is not an error: the alert is stored with
status ``skipped`` so the UI can show that nothing was delivered.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy.orm import Session

from api.config import settings
from api.models import AlertEvent


def _post(url: str, payload: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        response = httpx.post(url, json=payload, timeout=8.0)
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}"
        return True, None
    except httpx.HTTPError as exc:
        return False, str(exc)


def _email(subject: str, body: str) -> tuple[bool, str | None]:
    if not settings.alert_email_enabled or not settings.smtp_host or not settings.alert_email_to:
        return False, "email alerts not configured"
    message = EmailMessage()
    message["Subject"] = f"[Nullpoint] {subject}"
    message["From"] = settings.smtp_user or "alerts@nullpoint.dev"
    message["To"] = settings.alert_email_to
    message.set_content(body + "\n\nResearch only. Not investment advice.")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            server.send_message(message)
        return True, None
    except Exception as exc:
        return False, str(exc)


def dispatch(
    session: Session,
    org_id: str,
    event: str,
    subject: str,
    body: str,
    *,
    channels: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Send to every configured channel and persist one row per channel attempt."""
    channels = channels or ["log"]
    if settings.slack_webhook_url and "slack" not in channels:
        channels.append("slack")
    if settings.discord_webhook_url and "discord" not in channels:
        channels.append("discord")
    if settings.alert_email_enabled and "email" not in channels:
        channels.append("email")

    results: list[dict[str, Any]] = []
    for channel in channels:
        ok, error = True, None
        if channel == "slack" and settings.slack_webhook_url:
            ok, error = _post(
                settings.slack_webhook_url.get_secret_value(),
                {"text": f"*{subject}*\n{body}"},
            )
        elif channel == "discord" and settings.discord_webhook_url:
            ok, error = _post(
                settings.discord_webhook_url.get_secret_value(),
                {"content": f"**{subject}**\n{body}"},
            )
        elif channel == "email":
            ok, error = _email(subject, body)
        elif channel == "log":
            ok, error = True, None

        row = AlertEvent(
            org_id=org_id,
            channel=channel,
            event=event,
            subject=subject[:200],
            body=body[:8000],
            status="sent" if ok else ("failed" if error else "skipped"),
            error=error,
        )
        session.add(row)
        results.append({"channel": channel, "ok": ok, "error": error})
    session.commit()
    return results


def alert_callback(session: Session, org_id: str):  # type: ignore[no-untyped-def]
    def _alert(event: str, subject: str, body: str) -> None:
        try:
            dispatch(session, org_id, event, subject, body)
        except Exception:
            session.rollback()

    return _alert
