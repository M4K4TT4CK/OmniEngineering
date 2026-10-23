import hashlib
import hmac
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app import WebhookServer, create_order

SECRET = "test-secret"


def sign(body):
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class WebhookTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "orders.db")
        self.server = WebhookServer(("127.0.0.1", 0), self.db_path, SECRET)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        create_order(self.db_path, "order-1", 1500)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def deliver(self, event, signature=None):
        body = json.dumps(event).encode()
        if signature is None:
            signature = sign(body)
        return self.post_raw(body, signature)

    def post_raw(self, body, signature):
        req = urllib.request.Request(
            f"{self.base}/webhooks/payments",
            data=body,
            method="POST",
            headers={"X-Signature": signature or "", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def order(self, order_id="order-1"):
        try:
            with urllib.request.urlopen(f"{self.base}/orders/{order_id}") as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def event(self, event_id, etype, amount=1500, order_id="order-1"):
        return {"id": event_id, "type": etype, "order_id": order_id, "amount": amount}


class WebhookTests(WebhookTestCase):
    def test_succeeded_marks_order_paid(self):
        status, body = self.deliver(self.event("evt-1", "payment.succeeded"))
        self.assertEqual((status, body["status"]), (200, "applied"))

        status, order = self.order()
        self.assertEqual(status, 200)
        self.assertEqual(order["status"], "paid")
        self.assertEqual(order["amount"], 1500)

    def test_failed_marks_pending_order_failed(self):
        self.deliver(self.event("evt-1", "payment.failed"))
        self.assertEqual(self.order()[1]["status"], "failed")

    def test_refund_after_payment(self):
        self.deliver(self.event("evt-1", "payment.succeeded"))
        status, body = self.deliver(self.event("evt-2", "refund.created"))
        self.assertEqual((status, body["status"]), (200, "applied"))
        self.assertEqual(self.order()[1]["status"], "refunded")

    def test_redelivered_event_is_not_reapplied(self):
        self.deliver(self.event("evt-1", "payment.succeeded"))
        self.deliver(self.event("evt-2", "refund.created"))

        # Replaying the original success must not flip the refunded order back.
        status, body = self.deliver(self.event("evt-1", "payment.succeeded"))
        self.assertEqual((status, body["status"]), (200, "duplicate"))
        self.assertEqual(self.order()[1]["status"], "refunded")

    def test_concurrent_duplicates_applied_once(self):
        event = self.event("evt-1", "payment.succeeded")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.deliver(event), range(16)))
        statuses = [body["status"] for _, body in results]
        self.assertTrue(all(code == 200 for code, _ in results))
        self.assertEqual(statuses.count("applied"), 1)
        self.assertEqual(statuses.count("duplicate"), 15)

    def test_refund_before_payment_is_ignored(self):
        status, body = self.deliver(self.event("evt-1", "refund.created"))
        self.assertEqual((status, body["status"]), (200, "ignored"))
        self.assertEqual(self.order()[1]["status"], "pending")

    def test_unknown_event_type_is_ignored(self):
        status, body = self.deliver(self.event("evt-1", "payout.sent"))
        self.assertEqual((status, body["status"]), (200, "ignored"))

    def test_amount_mismatch_rejected_and_not_recorded(self):
        status, _ = self.deliver(self.event("evt-1", "payment.succeeded", amount=999))
        self.assertEqual(status, 422)
        self.assertEqual(self.order()[1]["status"], "pending")

        # Not recorded, so a corrected delivery with the same id still applies.
        status, body = self.deliver(self.event("evt-1", "payment.succeeded"))
        self.assertEqual((status, body["status"]), (200, "applied"))

    def test_unknown_order_returns_404_and_retry_applies_later(self):
        event = self.event("evt-9", "payment.succeeded", order_id="order-2")
        status, _ = self.deliver(event)
        self.assertEqual(status, 404)

        create_order(self.db_path, "order-2", 1500)
        status, body = self.deliver(event)
        self.assertEqual((status, body["status"]), (200, "applied"))
        self.assertEqual(self.order("order-2")[1]["status"], "paid")

    def test_bad_signature_rejected(self):
        event = self.event("evt-1", "payment.succeeded")
        status, _ = self.deliver(event, signature="00" * 32)
        self.assertEqual(status, 401)

        status, _ = self.deliver(event, signature="")
        self.assertEqual(status, 401)
        self.assertEqual(self.order()[1]["status"], "pending")

    def test_signature_over_different_body_rejected(self):
        body = json.dumps(self.event("evt-1", "payment.succeeded")).encode()
        tampered = body.replace(b'"order-1"', b'"order-2"')
        status, _ = self.post_raw(tampered, sign(body))
        self.assertEqual(status, 401)

    def test_malformed_body_with_valid_signature_rejected(self):
        for body in (b"not json", b"[]", b'{"id": "", "type": "payment.succeeded"}'):
            status, _ = self.post_raw(body, sign(body))
            self.assertEqual(status, 400, body)

    def test_invalid_amount_rejected(self):
        for amount in (-1, "1500", True, 1.5):
            status, _ = self.deliver(self.event("evt-1", "payment.succeeded", amount=amount))
            self.assertEqual(status, 400, amount)

    def test_unknown_order_lookup_returns_404(self):
        status, _ = self.order("missing")
        self.assertEqual(status, 404)

    def test_unknown_path_returns_404(self):
        status, _ = self.post_raw(b"{}", sign(b"{}"))
        self.assertEqual(status, 400)  # signed, but not an event
        req = urllib.request.Request(f"{self.base}/nope", method="POST", data=b"")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
