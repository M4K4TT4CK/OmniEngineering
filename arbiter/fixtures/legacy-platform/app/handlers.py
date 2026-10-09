from .config import QUEUE_URL


def handle(event, context):
    if not event:
        return {"status": 400}
    return {"status": 200, "queue": QUEUE_URL}
