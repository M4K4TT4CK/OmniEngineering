"""Mail delivery with the server certificate verified (ARB-083)."""
import smtplib
import ssl
from email.message import EmailMessage


def send(host: str, port: int, user: str, password: str, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=30)
    with client as smtp:
        if port != 465:
            smtp.starttls(context=context)
        smtp.login(user, password)
        smtp.send_message(msg)
