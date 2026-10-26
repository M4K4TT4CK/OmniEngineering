"""Domain objects for orders.

Kept free of I/O so pricing stays testable."""


class Base:
    """Common identity for stored records."""

    def key(self) -> str:
        """Stable storage key."""
        return type(self).__name__


class Item(Base):
    """A product line with a unit price."""

    def __init__(self, name: str, price: int) -> None:
        self.name = name
        self.price = price


class Order(Base):
    """A customer's basket of items."""

    def __init__(self) -> None:
        self.items: list[Item] = []

    def add(self, name: str, price: int) -> Item:
        """Append an item and return it."""
        item = Item(name, price)
        self.items.append(item)
        return item

    def total(self) -> int:
        """Sum of item prices."""
        return sum(i.price for i in self.items)
