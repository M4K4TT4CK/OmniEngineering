"""Session tokens, password reset codes and password storage."""
import hashlib
import hmac
import os
import random
import secrets

WORKERS = ("w1", "w2", "w3")


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def issue_reset(user) -> str:
    reset_code = f"{secrets.randbelow(1_000_000):06d}"
    user.reset_code = reset_code
    return reset_code


def pick_worker() -> str:
    # Load spreading, not a secret: `random` is the right tool here.
    return random.choice(WORKERS)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_password(stored: str, candidate: str) -> bool:
    salt_hex, digest_hex = stored.split(":", 1)
    digest = hashlib.scrypt(candidate.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


def content_etag(body: bytes) -> str:
    # Not a password: a weak digest is fine as a cache key.
    return hashlib.sha1(body).hexdigest()


def verify_signature(payload: bytes, signature: str, key: bytes) -> bool:
    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
