# TODO: replace with the shared library
# FIXME: this loses precision
# TODO: handle timezones
# XXX: not thread safe
# TODO: remove once the migration lands


def chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]
