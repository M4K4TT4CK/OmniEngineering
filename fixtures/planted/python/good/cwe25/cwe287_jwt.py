"""CWE-287: read the caller's identity from a verified bearer token."""
import os

import jwt
from flask import Flask, request

app = Flask(__name__)


def current_user_id() -> str:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"], audience="api")
    return claims["sub"]


@app.get("/me")
def me() -> dict:
    return {"user": current_user_id()}
