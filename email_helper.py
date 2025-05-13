# email_helper.py

import os
import ssl
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

# Load SMTP_USER and SMTP_PASS from .env
load_dotenv()

SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")       # support@msistaff.com
SMTP_PASS = os.getenv("SMTP_PASS")       # your mailbox password
FROM_ADDR = SMTP_USER

if not SMTP_USER or not SMTP_PASS:
    raise RuntimeError(
        "Missing SMTP_USER or SMTP_PASS in environment. "
        "Check your .env file."
    )

def send_email(to: str, subject: str, html: str, text: str | None = None):
    """
    Send a multipart (plain-text + HTML) email via Office365 SMTP.
    """
    msg = EmailMessage()
    msg["From"] = FROM_ADDR
    msg["To"] = to
    msg["Subject"] = subject

    # Plain-text fallback
    msg.set_content(text or "Please view this message in an HTML-capable client.")
    # HTML version
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls(context=ctx)
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
