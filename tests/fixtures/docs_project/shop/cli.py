"""Command-line entry point."""

import sys

from shop.service import checkout, describe, format_total

try:  # optional: the graph does not record imports inside try blocks, omni docs reads them from the source
    from shop.models import Order
except ImportError:  # pragma: no cover
    Order = None


def main() -> int:
    """Run checkout over argv."""
    total = checkout(sys.argv[1:])
    print(describe(total))
    print(format_total(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
