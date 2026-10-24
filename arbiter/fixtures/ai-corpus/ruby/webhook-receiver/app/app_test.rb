ENV["APP_ENV"] = "test"

require "minitest/autorun"
require "rack/test"
require_relative "app"

class WebhookAppTest < Minitest::Test
  include Rack::Test::Methods

  SECRET = "shh-secret".freeze
  TOKEN = "admin-token".freeze

  def setup
    @store = Store.new(":memory:")
    @store.upsert_merchant("m1", "https://merchant.example/callback")
    @forwarded = []
    @forward_result = true
    forwarded = @forwarded
    result = -> { @forward_result }
    forwarder = ->(url, body) { forwarded << [url, body]; result.call }

    store = @store
    @app = Class.new(WebhookApp) do
      set :store, store
      set :secret, SECRET
      set :admin_token, TOKEN
      set :forwarder, forwarder
    end
  end

  def app = @app

  def sign(body, secret = SECRET) = OpenSSL::HMAC.hexdigest("SHA256", secret, body)

  def event(overrides = {})
    { "id" => "evt_1", "type" => "payment.succeeded", "merchant_id" => "m1", "amount" => 500 }.merge(overrides)
  end

  def post_event(body, signature: sign(body))
    header "X-Signature", signature if signature
    post "/webhooks", body, "CONTENT_TYPE" => "application/json"
  end

  def test_valid_success_event_is_stored_and_forwarded
    body = JSON.generate(event)
    post_event(body)

    assert_equal 200, last_response.status
    assert_equal [["https://merchant.example/callback", body]], @forwarded
    assert_equal "delivered", @store.recent_events("m1", 10).first["forward_status"]
  end

  def test_accepts_sha256_prefix
    body = JSON.generate(event)
    post_event(body, signature: "sha256=#{sign(body)}")
    assert_equal 200, last_response.status
  end

  def test_bad_signature_rejected_and_not_stored
    body = JSON.generate(event)
    post_event(body, signature: sign(body, "wrong"))

    assert_equal 401, last_response.status
    assert_empty @store.recent_events("m1", 10)
    assert_empty @forwarded
  end

  def test_missing_signature_rejected
    post_event(JSON.generate(event), signature: nil)
    assert_equal 401, last_response.status
  end

  def test_tampered_body_rejected
    body = JSON.generate(event)
    post_event(body.sub("500", "1"), signature: sign(body))
    assert_equal 401, last_response.status
  end

  def test_non_success_event_stored_but_not_forwarded
    post_event(JSON.generate(event("type" => "payment.failed")))

    assert_equal 200, last_response.status
    assert_empty @forwarded
    assert_equal "none", @store.recent_events("m1", 10).first["forward_status"]
  end

  def test_forward_failure_is_recorded_and_still_acknowledged
    @forward_result = false
    post_event(JSON.generate(event))

    assert_equal 200, last_response.status
    assert_equal "failed", @store.recent_events("m1", 10).first["forward_status"]
  end

  def test_duplicate_event_not_stored_or_forwarded_twice
    body = JSON.generate(event)
    post_event(body)
    post_event(body)

    assert_equal 200, last_response.status
    assert_equal "duplicate", JSON.parse(last_response.body)["status"]
    assert_equal 1, @forwarded.size
    assert_equal 1, @store.recent_events("m1", 10).size
  end

  def test_invalid_json_and_missing_fields
    post_event("not json")
    assert_equal 422, last_response.status

    post_event(JSON.generate("id" => "x"))
    assert_equal 422, last_response.status
  end

  def test_unknown_merchant
    post_event(JSON.generate(event("merchant_id" => "nope")))
    assert_equal 404, last_response.status
  end

  def test_list_events_requires_token
    get "/merchants/m1/events"
    assert_equal 401, last_response.status

    header "Authorization", "Bearer wrong"
    get "/merchants/m1/events"
    assert_equal 401, last_response.status
  end

  def test_list_events_returns_recent_for_merchant_only
    @store.upsert_merchant("m2", "https://other.example/cb")
    %w[a b c].each { |i| post_event(JSON.generate(event("id" => i))) }
    post_event(JSON.generate(event("id" => "z", "merchant_id" => "m2")))

    header "Authorization", "Bearer #{TOKEN}"
    get "/merchants/m1/events?limit=2"

    assert_equal 200, last_response.status
    events = JSON.parse(last_response.body)["events"]
    assert_equal %w[c b], events.map { |e| e["id"] }
    assert_equal 500, events.first["payload"]["amount"]
  end

  def test_fails_closed_when_secret_unset
    store = @store
    open_app = Class.new(WebhookApp) { set :store, store; set :secret, nil }
    body = JSON.generate(event)
    session = Rack::Test::Session.new(Rack::MockSession.new(open_app))
    session.header "X-Signature", OpenSSL::HMAC.hexdigest("SHA256", "", body)
    session.post "/webhooks", body
    assert_equal 401, session.last_response.status
  end

  def test_store_rejects_non_http_callback_url
    assert_raises(ArgumentError) { @store.upsert_merchant("m3", "file:///etc/passwd") }
  end
end
