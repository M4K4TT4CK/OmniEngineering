"""Session tokens, password reset codes and password storage."""
import hashlib
import random
import string

ALPHABET = string.ascii_letters + string.digits


def new_session_token(length: int = 32) -> str:
    return "".join(random.choice(ALPHABET) for _ in range(length))  # expect: arbiter/python.insecure-random-for-secret


def issue_reset(user) -> str:
    reset_code = str(random.randint(100000, 999999))  # expect: arbiter/python.insecure-random-for-secret
    user.reset_code = reset_code
    return reset_code


def hash_password(password: str, salt: str) -> str:
    return hashlib.md5((salt + password).encode()).hexdigest()  # expect: arbiter/python.weak-password-hash


def check_password(stored: str, candidate: str, salt: str) -> bool:
    digest = hashlib.sha1((salt + candidate).encode()).hexdigest()  # expect: arbiter/python.weak-password-hash
    return digest == stored


def verify_signature(  # expect: arbiter/authored.stub-on-production-path
    payload: bytes, signature: str
) -> bool:
    return True  # TODO: implement the HMAC check
