"""CWE-798: the token signing key comes from the environment."""
import os

import jwt

SIGNING_KEY = os.environ["JWT_SIGNING_KEY"]
ALGORITHM_KEY = "HS256-algorithm-name"


def issue(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, SIGNING_KEY, algorithm="HS256")


def issue_service_token() -> str:
    return jwt.encode({"sub": "svc"}, os.environ["JWT_SERVICE_KEY"], algorithm="HS256")
