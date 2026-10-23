"""Mail delivery as model-written job handlers ship it (ARB-083)."""
import smtplib
from email.message import EmailMessage


def send(host: str, port: int, user: str, password: str, msg: EmailMessage) -> None:
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=30)  # expect: arbiter/python.mail-tls-unverified
    else:
        client = smtplib.SMTP(host, port, timeout=30)
    with client as smtp:
        if port != 465:
            smtp.starttls()  # expect: arbiter/python.mail-tls-unverified
        smtp.login(user, password)
        smtp.send_message(msg)
