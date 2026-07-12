"""Shared SMTP sender (Gmail app password over SSL).

Secrets live only as Actions secrets: DIGEST_SMTP_USER / DIGEST_SMTP_PASS.
Missing creds -> ``send_email`` returns False and the caller skips cleanly.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def creds_present() -> bool:
    return bool(os.environ.get("DIGEST_SMTP_USER") and os.environ.get("DIGEST_SMTP_PASS"))


def send_email(recipient: str, subject: str, html: str, text: str) -> bool:
    user = os.environ.get("DIGEST_SMTP_USER")
    password = os.environ.get("DIGEST_SMTP_PASS")
    if not (user and password and recipient):
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(user, password)
        server.sendmail(user, [recipient], msg.as_string())
    return True
