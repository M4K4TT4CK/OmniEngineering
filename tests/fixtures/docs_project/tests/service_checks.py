"""Checks for the order service (fixture data, not part of the omni suite)."""

from shop.service import checkout


def check_checkout() -> None:
    assert checkout(["a", "b"]) == 2
