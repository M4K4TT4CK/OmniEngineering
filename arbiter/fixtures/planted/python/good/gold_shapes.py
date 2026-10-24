"""Shapes taken from the gold benchmark and OWASP Benchmark Python (ARB-065)."""
import hashlib
import os
import secrets
from typing import TYPE_CHECKING

from flask import session

if TYPE_CHECKING:
    # Read by the type checker only, so not a runtime requirement (round 2).
    from typing_extensions import Self

MAX_ROWS = 500


def purge_owner(cursor, connection, table: str, owner: str) -> None:
    # Identifiers go through quote_name; the value stays a bind parameter.
    qn = connection.ops.quote_name
    cursor.execute("DELETE FROM %s WHERE owner = %%s" % qn(table), (owner,))
    cursor.execute("SELECT %s FROM %s LIMIT %s" % (qn("id"), qn(table), MAX_ROWS))


def purge_table(cursor, connection, table: str, owner: str, mine: bool) -> None:
    # Every assignment to the spliced local is a quoted name (round 2).
    target = connection.ops.quote_name(table)
    if not mine:
        target = connection.ops.quote_name(table + "_archive")
    cursor.execute("DELETE FROM %s" % target)


def remember_me(response):
    value = secrets.token_urlsafe(32)
    session["remember_me"] = value
    response.set_cookie("remember_me", value, secure=True, httponly=True)
    return response


def store_credential(secret_value: bytes) -> None:
    salt = os.urandom(16)
    digest = hashlib.scrypt(secret_value, salt=salt, n=2**14, r=8, p=1)
    with open("passwords.txt", "a", encoding="utf-8") as f:
        f.write(salt.hex() + ":" + digest.hex() + "\n")


def lookup(expr: str, names: dict):
    # Only a bare name reaches eval, and it can only look one up.
    if not expr.isidentifier():
        raise ValueError(f"not a name: {expr!r}")
    return eval(expr, {"__builtins__": {}}, names)
