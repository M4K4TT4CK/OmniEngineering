"""CWE-798: the token signing key is a literal in the module."""
import jwt

SIGNING_KEY = "k3y-th4t-sh0uld-n0t-l1ve-h3re"  # expect: arbiter/python.jwt-literal-signing-key


def issue(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, SIGNING_KEY, algorithm="HS256")


def issue_service_token() -> str:
    return jwt.encode({"sub": "svc"}, "an0ther-l1teral-k3y-1n-c0de", algorithm="HS256")  # expect: arbiter/python.jwt-literal-signing-key
