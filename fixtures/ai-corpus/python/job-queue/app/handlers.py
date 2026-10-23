"""Built-in job types: resize-image and send-email."""

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from jobqueue import JobType


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value


def _optional_dimension(payload: dict, key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"'{key}' must be a positive integer")
    return value


def _resolve_image_path(raw: str) -> Path:
    """Resolve the path, confining it to IMAGE_ROOT when that variable is set."""
    root = os.environ.get("IMAGE_ROOT")
    if not root:
        return Path(raw).resolve()
    root_path = Path(root).resolve()
    path = (root_path / raw).resolve()  # absolute `raw` replaces root; checked below
    if not path.is_relative_to(root_path):
        raise ValueError("path is outside IMAGE_ROOT")
    return path


def validate_resize(payload: dict) -> None:
    raw = _require_str(payload, "path")
    _optional_dimension(payload, "width", 1024)
    _optional_dimension(payload, "height", 1024)
    _resolve_image_path(raw)


def resize_image(payload: dict) -> dict:
    """Shrink the image to fit within width x height, keeping aspect ratio.

    Writes `<name>_resized<ext>` next to the original; the original is untouched.
    """
    from PIL import Image  # imported lazily so email-only deployments don't need Pillow

    src = _resolve_image_path(payload["path"])
    box = (_optional_dimension(payload, "width", 1024),
           _optional_dimension(payload, "height", 1024))
    dest = src.with_name(f"{src.stem}_resized{src.suffix}")
    with Image.open(src) as img:
        img.thumbnail(box)
        img.save(dest)
        size = img.size
    return {"output_path": str(dest), "width": size[0], "height": size[1]}


def validate_email(payload: dict) -> None:
    for key in ("recipient", "subject", "body"):
        if not isinstance(payload.get(key), str):
            raise ValueError(f"'{key}' is required and must be a string")
    if not payload["recipient"]:
        raise ValueError("'recipient' must not be empty")


def send_email(payload: dict) -> dict:
    """Send via SMTP. Settings: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM, SMTP_STARTTLS (default true; ignored on port 465, which uses SSL)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST is not configured")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or user or "noreply@localhost"
    starttls = os.environ.get("SMTP_STARTTLS", "true").lower() not in ("0", "false", "no")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = payload["recipient"]
    msg["Subject"] = payload["subject"]
    msg.set_content(payload["body"])

    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        client = smtplib.SMTP(host, port, timeout=30)
    with client as smtp:
        if port != 465 and starttls:
            smtp.starttls()
        if user:
            smtp.login(user, password or "")
        smtp.send_message(msg)
    return {"recipient": payload["recipient"]}


JOB_TYPES = {
    "resize-image": JobType(resize_image, validate_resize),
    "send-email": JobType(send_email, validate_email),
}
