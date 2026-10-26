"""Command-line entry point."""

import sys

from shop.service import checkout, describe, format_total


def main() -> int:
    """Run checkout over argv."""
    total = checkout(sys.argv[1:])
    print(describe(total))
    print(format_total(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
