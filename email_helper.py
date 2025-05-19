# email_helper.py
import os
import ssl
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from string import Template     # ← this line must be here
import datetime                 # ← and this one


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

def send_welcome_email(to: str, first_name: str, temp_password: str, reset_link: str):
    # 1. Load the HTML template
    with open('templates/welcome_email.html', 'r', encoding='utf-8') as f:
        tpl = Template(f.read())

    # 2. Fill in our placeholders
    html_body = tpl.substitute(
        first_name=first_name,
        temp_password=temp_password,
        reset_link=reset_link,
        year=datetime.datetime.now().year
    )

    # 3. Create a simple plain-text fallback
    text_body = f"""\
Welcome to MSI Ticketing!

Hi {first_name},

Your temporary password is: {temp_password}

Set your password here: {reset_link}

Thanks,
MSI IT Support Team
"""

    # 4. Use our send_email() to dispatch
    send_email(
        to=to,
        subject="Your MSI Ticketing Account — Set Your Password",
        html=html_body,
        text=text_body
    )
