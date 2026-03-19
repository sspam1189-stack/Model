# scripts/email_report.py
import os
from smtplib import SMTP_SSL
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(subject, text, html=None):
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("TO_EMAIL")

    if not user or not password or not to:
        raise RuntimeError("Missing env vars: GMAIL_USER, GMAIL_APP_PASSWORD, TO_EMAIL")

    msg = MIMEMultipart("alternative")
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject

    if text:
        msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    with SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.sendmail(user, to, msg.as_string())
