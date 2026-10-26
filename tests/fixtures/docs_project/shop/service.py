"""Order service: the use cases the CLI calls."""

from shop.models import Order


def checkout(names: list[str]) -> int:
    """Price a list of item names at 1 each and return the total."""
    order = Order()
    for name in names:
        order.add(name, 1)
    return order.total()


def describe(total: int) -> str:
    """Human-readable total."""
    return format_total(total)


def format_total(total: int) -> str:
    """Format a total in cents."""
    return f"{total} cents"
