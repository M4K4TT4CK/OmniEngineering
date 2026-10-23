# frozen_string_literal: true

require "minitest/autorun"
require "rack/test"
require "json"
require "openssl"
require_relative "app"

class PaymentWebhookAppTest < Minitest::Test
  include Rack::Test::Methods

  SECRET = "test-secret"

  def app
    PaymentWebhookApp
  end

  def setup
    PaymentWebhookApp.set :secret, SECRET
    PaymentWebhookApp.set :database_path, ":memory:"
    PaymentWebhookApp.set :database, nil # fresh database for each test
  end

  def test_applies_payment_succeeded
    post_event(event(id: "evt-1", type: "payment.succeeded"))

    assert_equal 200, last_response.status
    assert_equal "applied", json(last_response)["result"]
    assert_equal "paid", order_status("order-1")
  end

  def test_rejects_bad_signature_without_touching_orders
    body = JSON.generate(event(id: "evt-1", type: "payment.succeeded"))
    post_event_raw body, signature: "sha256=#{'0' * 64}"

    assert_equal 401, last_response.status
    assert_equal 404, get("/orders/order-1").status
  end

  def test_rejects_missing_signature
    post "/webhooks/payments", JSON.generate(event(id: "evt-1", type: "payment.succeeded")), "CONTENT_TYPE" => "application/json"

    assert_equal 401, last_response.status
  end

  def test_rejects_tampered_body
    signature = sign(JSON.generate(event(id: "evt-1", type: "payment.succeeded", amount: 100)))
    post_event_raw JSON.generate(event(id: "evt-1", type: "payment.succeeded", amount: 999_999)), signature: signature

    assert_equal 401, last_response.status
  end

  def test_rejects_everything_when_secret_is_unset
    PaymentWebhookApp.set :secret, nil
    post_event(event(id: "evt-1", type: "payment.succeeded"))

    assert_equal 401, last_response.status
  end

  def test_duplicate_deliveries_are_applied_once
    succeeded = event(id: "evt-1", type: "payment.succeeded")
    refund = event(id: "evt-2", type: "refund.created")

    post_event(succeeded)
    post_event(refund)
    assert_equal "refunded", order_status("order-1")

    post_event(succeeded)
    assert_equal "duplicate", json(last_response)["result"]

    post_event(refund)
    assert_equal "duplicate", json(last_response)["result"]

    assert_equal "refunded", order_status("order-1")
  end

  def test_redelivery_after_a_later_status_does_not_roll_back_the_order
    failed = event(id: "evt-1", type: "payment.failed")
    post_event(failed)
    post_event(event(id: "evt-2", type: "payment.succeeded"))
    post_event(failed)

    assert_equal "duplicate", json(last_response)["result"]
    assert_equal "failed", order_status("order-1")
  end

  def test_refund_before_payment_is_ignored
    post_event(event(id: "evt-1", type: "refund.created"))

    assert_equal "ignored", json(last_response)["result"]
    assert_equal "pending", order_status("order-1")
  end

  def test_payment_failed_after_success_is_ignored
    post_event(event(id: "evt-1", type: "payment.succeeded"))
    post_event(event(id: "evt-2", type: "payment.failed"))

    assert_equal "ignored", json(last_response)["result"]
    assert_equal "paid", order_status("order-1")
  end

  def test_unsupported_event_type_is_not_recorded
    post_event(event(id: "evt-1", type: "dispute.opened"))

    assert_equal 200, last_response.status
    assert_equal "unsupported", json(last_response)["result"]
    assert_equal 404, get("/orders/order-1").status
  end

  def test_malformed_json_is_rejected
    post_event_raw "{not json", signature: sign("{not json")

    assert_equal 400, last_response.status
  end

  def test_non_integer_amount_is_rejected
    post_event(event(id: "evt-1", type: "payment.succeeded", amount: "15.00"))

    assert_equal 400, last_response.status
  end

  def test_missing_fields_are_rejected
    post_event({ "type" => "payment.succeeded", "amount" => 1500 })

    assert_equal 400, last_response.status
  end

  def test_order_lookup_returns_status_and_amount
    post_event(event(id: "evt-1", type: "payment.succeeded", amount: 2500))
    get "/orders/order-1"

    assert_equal 200, last_response.status
    body = json(last_response)
    assert_equal "order-1", body["id"]
    assert_equal 2500, body["amount"]
    assert_equal "paid", body["status"]
  end

  def test_unknown_order_lookup_returns_404
    get "/orders/nope"

    assert_equal 404, last_response.status
  end

  private

  def event(id:, type:, order_id: "order-1", amount: 1500)
    { "id" => id, "type" => type, "order_id" => order_id, "amount" => amount }
  end

  def sign(body)
    "sha256=#{OpenSSL::HMAC.hexdigest('SHA256', SECRET, body)}"
  end

  def post_event(payload)
    post_event_raw JSON.generate(payload), signature: sign(JSON.generate(payload))
  end

  def post_event_raw(body, signature:)
    post "/webhooks/payments", body,
         "CONTENT_TYPE" => "application/json",
         "HTTP_X_PROVIDER_SIGNATURE" => signature
  end

  def json(response)
    JSON.parse(response.body)
  end

  def order_status(order_id)
    get "/orders/#{order_id}"
    json(last_response)["status"]
  end
end
