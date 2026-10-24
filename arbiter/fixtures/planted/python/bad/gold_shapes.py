"""Shapes taken from the gold benchmark and OWASP Benchmark Python (ARB-065)."""
import hashlib
import random

from flask import session


def purge_owner(cursor, connection, table: str, owner: str) -> None:
    qn = connection.ops.quote_name
    cursor.execute("DELETE FROM %s WHERE owner = '%s'" % (qn(table), owner))  # expect: arbiter/python.sql-injection


def purge_table(cursor, connection, table: str, owner: str, mine: bool) -> None:
    # Quoted on one path only: one raw assignment makes the local unsafe.
    target = connection.ops.quote_name(table)
    if not mine:
        target = owner
    cursor.execute("DELETE FROM %s" % target)  # expect: arbiter/python.sql-injection


def remember_me(response):
    value = str(random.random())[2:]  # expect: arbiter/python.insecure-random-for-secret
    session["remember_me"] = value
    response.set_cookie("remember_me", value)
    return response


def store_credential(secret_value: bytes) -> None:
    digest = hashlib.md5()  # expect: arbiter/python.weak-password-hash
    digest.update(secret_value)
    with open("passwords.txt", "a", encoding="utf-8") as f:
        f.write(digest.hexdigest() + "\n")


def lookup(expr: str, names: dict):
    return eval(expr, {"__builtins__": {}}, names)  # expect: arbiter/python.code-injection
