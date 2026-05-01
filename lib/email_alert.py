"""
SMTP alert sender for MaxPain — supports plain-text and HTML bodies.

Lifted from Metal_Project/scripts/pipeline/cron_alert.py with multipart
HTML extension. Reads SMTP credentials from Metal_Project/config/api_keys.env
(same env shared via Tranche 1 of the migration).

Usage:
    from lib.email_alert import send_html_alert
    send_html_alert("MaxPain Daily Alert — KRE actionable",
                    text_body, html_body)
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ENV_PATH = Path.home() / "Metal_Project/config/api_keys.env"


def _load_env() -> dict[str, str]:
    """Read KEY=VALUE pairs from api_keys.env. Lightweight inline loader to
    avoid importing Metal_Project.config.paths (which collides with
    scripts/backtest/config.py when both are on sys.path)."""
    env: dict[str, str] = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def send_html_alert(subject: str, text_body: str, html_body: str | None = None) -> bool:
    """Send a multipart email (text + optional HTML alternative).

    Returns True on success, False on configuration / network failure.
    Never raises — alerting failure should not crash the cron job.
    """
    try:
        env = _load_env()
        smtp_from = env.get("ALERT_EMAIL_FROM", "").strip()
        smtp_pass = env.get("ALERT_EMAIL_PASSWORD", "").strip()
        smtp_to = env.get("ALERT_EMAIL_TO", "").strip()
        smtp_server = env.get("ALERT_EMAIL_SMTP_SERVER", "smtp.mail.yahoo.com").strip()
        smtp_port = int(env.get("ALERT_EMAIL_SMTP_PORT", "587"))

        if not smtp_from or not smtp_pass or not smtp_to:
            print("  WARNING: alert email not configured in api_keys.env")
            return False

        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            msg = MIMEText(text_body, "plain", "utf-8")

        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = smtp_to

        server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
        server.starttls()
        server.login(smtp_from, smtp_pass)
        server.send_message(msg)
        server.quit()
        print(f"  Alert sent: {subject}")
        return True
    except Exception as e:
        print(f"  WARNING: alert email failed: {e}")
        return False
