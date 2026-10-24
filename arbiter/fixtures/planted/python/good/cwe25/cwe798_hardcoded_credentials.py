"""CWE-798: database and mail credentials read from the environment."""
import os
import smtplib
import ssl

from sqlalchemy import create_engine

engine = create_engine(os.environ["DATABASE_URL"])
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]


def send_alert(body: str) -> None:
    with smtplib.SMTP_SSL("smtp.internal", 465, timeout=10, context=ssl.create_default_context()) as smtp:
        smtp.login("alerts", SMTP_PASSWORD)
        smtp.sendmail("alerts@internal", ["oncall@internal"], body)
