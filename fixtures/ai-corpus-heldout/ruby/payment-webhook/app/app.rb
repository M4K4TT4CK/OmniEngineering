# frozen_string_literal: true

require "json"
require "openssl"
require "time"
require "sinatra/base"
require "sqlite3"

class PaymentWebhookApp < Sinatra::Base
  SCHEMA = <<~SQL
    CREATE TABLE IF NOT EXISTS orders (
      id          TEXT PRIMARY KEY,
      amount      INTEGER NOT NULL,
      status      TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS processed_events (
      id           TEXT PRIMARY KEY,
      type         TEXT NOT NULL,
      order_id     TEXT NOT NULL,
      outcome      TEXT NOT NULL,
      processed_at TEXT NOT NULL
    );
  SQL

  # event type => [statuses the order may be in, status it moves to]
  TRANSITIONS = {
    "payment.succeeded" => [["pending"], "paid"],
    "payment.failed" => [["pending"], "failed"],
    "refund.created" => [["paid"], "refunded"]
  }.freeze

  # One SQLite connection is shared across threads, so every DB access holds this lock.
  DB_LOCK = Mutex.new

  set :secret, ENV["WEBHOOK_SECRET"]
  set :database_path, ENV.fetch("DATABASE_PATH", "orders.sqlite3")
  set :database, nil

  before { content_type :json }

  post "/webhooks/payments" do
    body = request.body.read
    respond 401, error: "invalid signature" unless valid_signature?(body)

    event = parse_event(body)
    respond 400, error: "malformed event" unless event
    respond 200, result: "unsupported" unless TRANSITIONS.key?(event["type"])

    respond 200, result: apply_event(event)
  end

  get "/orders/:id" do
    order = DB_LOCK.synchronize do
      db.get_first_row("SELECT id, amount, status, updated_at FROM orders WHERE id = ?", params[:id])
    end
    respond 404, error: "not found" unless order

    respond 200, order
  end

  helpers do
    def db
      settings.database ||= open_database
    end

    def open_database
      SQLite3::Database.new(settings.database_path).tap do |conn|
        conn.results_as_hash = true
        conn.execute_batch(SCHEMA)
      end
    end

    def valid_signature?(body)
      return false if settings.secret.to_s.empty?

      expected = "sha256=#{OpenSSL::HMAC.hexdigest('SHA256', settings.secret, body)}"
      Rack::Utils.secure_compare(request.env["HTTP_X_PROVIDER_SIGNATURE"].to_s, expected)
    end

    def parse_event(body)
      event = JSON.parse(body)
      return nil unless event.is_a?(Hash)
      return nil unless event["id"].is_a?(String) && event["order_id"].is_a?(String)
      return nil unless event["type"].is_a?(String) && event["amount"].is_a?(Integer)

      event
    rescue JSON::ParserError
      nil
    end

    # Runs inside one IMMEDIATE transaction, so the duplicate check, the order
    # update and the event record commit or roll back together.
    def apply_event(event)
      result = nil
      DB_LOCK.synchronize do
        db.transaction(:immediate) { result = process_event(event) }
      end
      result
    end

    def process_event(event)
      return "duplicate" if db.get_first_value("SELECT 1 FROM processed_events WHERE id = ?", event["id"])

      now = Time.now.utc.iso8601
      db.execute(
        "INSERT OR IGNORE INTO orders (id, amount, status, updated_at) VALUES (?, ?, 'pending', ?)",
        [event["order_id"], event["amount"], now]
      )

      current = db.get_first_value("SELECT status FROM orders WHERE id = ?", event["order_id"])
      allowed_from, new_status = TRANSITIONS.fetch(event["type"])
      outcome = allowed_from.include?(current) ? "applied" : "ignored"

      if outcome == "applied"
        db.execute(
          "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
          [new_status, now, event["order_id"]]
        )
      end

      db.execute(
        "INSERT INTO processed_events (id, type, order_id, outcome, processed_at) VALUES (?, ?, ?, ?, ?)",
        [event["id"], event["type"], event["order_id"], outcome, now]
      )
      outcome
    end

    def respond(status, payload)
      halt status, JSON.generate(payload)
    end
  end

  run! if __FILE__ == $PROGRAM_NAME
end
