require "json"
require "net/http"
require "openssl"
require "sinatra/base"
require "sqlite3"
require "time"
require "uri"

# SQLite persistence. A single connection is shared, guarded by a mutex.
class Store
  def initialize(path)
    @db = SQLite3::Database.new(path)
    @db.results_as_hash = true
    @mutex = Mutex.new
    @db.execute_batch(<<~SQL)
      CREATE TABLE IF NOT EXISTS merchants (
        id           TEXT PRIMARY KEY,
        callback_url TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS events (
        id             TEXT PRIMARY KEY,
        merchant_id    TEXT NOT NULL,
        type           TEXT NOT NULL,
        payload        TEXT NOT NULL,
        received_at    TEXT NOT NULL,
        forward_status TEXT NOT NULL DEFAULT 'none'
      );
      CREATE INDEX IF NOT EXISTS events_merchant_idx ON events (merchant_id, received_at);
    SQL
  end

  def upsert_merchant(id, callback_url)
    uri = URI.parse(callback_url.to_s)
    raise ArgumentError, "callback_url must be http(s): #{callback_url}" unless %w[http https].include?(uri.scheme) && uri.host

    sync do
      @db.execute("INSERT INTO merchants (id, callback_url) VALUES (?, ?) " \
                  "ON CONFLICT(id) DO UPDATE SET callback_url = excluded.callback_url", [id, callback_url])
    end
  end

  def merchant(id)
    sync { @db.get_first_row("SELECT * FROM merchants WHERE id = ?", [id]) }
  end

  # Returns true if the event was new, false if its id was already stored.
  def insert_event(id:, merchant_id:, type:, payload:)
    sync do
      @db.execute("INSERT OR IGNORE INTO events (id, merchant_id, type, payload, received_at) VALUES (?, ?, ?, ?, ?)",
                  [id, merchant_id, type, payload, Time.now.utc.iso8601(3)])
      @db.changes == 1
    end
  end

  def set_forward_status(id, status)
    sync { @db.execute("UPDATE events SET forward_status = ? WHERE id = ?", [status, id]) }
  end

  def recent_events(merchant_id, limit)
    sync do
      @db.execute("SELECT * FROM events WHERE merchant_id = ? ORDER BY received_at DESC, rowid DESC LIMIT ?",
                  [merchant_id, limit])
    end
  end

  private

  def sync(&block) = @mutex.synchronize(&block)
end

# Posts the raw event body to a merchant callback URL. Returns true on 2xx.
class Forwarder
  def call(url, body)
    uri = URI.parse(url)
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5
    res = http.post(uri.request_uri, body, "Content-Type" => "application/json")
    res.is_a?(Net::HTTPSuccess)
  rescue StandardError
    false
  end
end

class WebhookApp < Sinatra::Base
  SUCCESS_TYPE = "payment.succeeded".freeze
  MAX_BODY_BYTES = 1_048_576

  set :store, nil        # Store instance
  set :secret, nil       # HMAC shared secret for provider webhooks
  set :admin_token, nil  # bearer token for the read API
  set :forwarder, Forwarder.new
  set :show_exceptions, false

  post "/webhooks" do
    raw = request.body.read(MAX_BODY_BYTES + 1).to_s
    halt json_response(413, error: "body too large") if raw.bytesize > MAX_BODY_BYTES
    halt json_response(401, error: "invalid signature") unless valid_signature?(raw, request.env["HTTP_X_SIGNATURE"])

    event = parse_event(raw)
    halt json_response(422, error: "invalid event") unless event

    merchant = settings.store.merchant(event["merchant_id"])
    halt json_response(404, error: "unknown merchant") unless merchant

    unless settings.store.insert_event(id: event["id"], merchant_id: event["merchant_id"],
                                       type: event["type"], payload: raw)
      return json_response(200, status: "duplicate")
    end

    if event["type"] == SUCCESS_TYPE
      ok = settings.forwarder.call(merchant["callback_url"], raw)
      settings.store.set_forward_status(event["id"], ok ? "delivered" : "failed")
    end

    json_response(200, status: "received")
  end

  get "/merchants/:id/events" do
    halt json_response(401, error: "unauthorized") unless admin?

    limit = params["limit"].to_i
    limit = 50 if limit <= 0
    limit = [limit, 100].min
    events = settings.store.recent_events(params["id"], limit).map do |row|
      { id: row["id"], type: row["type"], received_at: row["received_at"],
        forward_status: row["forward_status"], payload: JSON.parse(row["payload"]) }
    end
    json_response(200, events: events)
  end

  helpers do
    def json_response(status, body)
      content_type :json
      [status, JSON.generate(body)]
    end

    def valid_signature?(raw, header)
      secret = settings.secret.to_s
      return false if secret.empty? || header.nil?

      expected = OpenSSL::HMAC.hexdigest("SHA256", secret, raw)
      given = header.strip.downcase.delete_prefix("sha256=")
      Rack::Utils.secure_compare(expected, given)
    end

    def parse_event(raw)
      event = JSON.parse(raw)
      return nil unless event.is_a?(Hash)
      return nil unless %w[id type merchant_id].all? { |k| event[k].is_a?(String) && !event[k].empty? }

      event
    rescue JSON::ParserError
      nil
    end

    def admin?
      token = settings.admin_token.to_s
      given = request.env["HTTP_AUTHORIZATION"].to_s.delete_prefix("Bearer ")
      !token.empty? && Rack::Utils.secure_compare(token, given)
    end
  end
end
