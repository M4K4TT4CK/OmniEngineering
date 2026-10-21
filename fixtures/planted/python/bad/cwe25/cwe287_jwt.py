"""CWE-287: read the caller's identity from a bearer token."""
import jwt
from flask import Flask, request

app = Flask(__name__)


def current_user_id() -> str:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False})  # expect-ext: semgrep/semgrep-rules.python.jwt.security.unverified-jwt-decode
    return claims["sub"]


@app.get("/me")
def me() -> dict:
    return {"user": current_user_id()}
