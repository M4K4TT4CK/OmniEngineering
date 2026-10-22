"""CWE-798: database and mail credentials written into the module."""
import smtplib

from sqlalchemy import create_engine

engine = create_engine("postgresql://app_user:Tr1cky-Pr0d-Pw@db.internal:5432/app")  # expect: arbiter/secrets.pg-url expect-ext: semgrep/semgrep-rules.generic.secrets.security.detected-username-and-password-in-uri
SMTP_PASSWORD = "q8Vz!m2Lr7#tKw4P"  # expect: arbiter/secrets.assigned-credential expect-ext: bandit/B105


def send_alert(body: str) -> None:
    with smtplib.SMTP_SSL("smtp.internal", 465, timeout=10) as smtp:
        smtp.login("alerts", SMTP_PASSWORD)
        smtp.sendmail("alerts@internal", ["oncall@internal"], body)
