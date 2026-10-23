require "sinatra/base"
require "sqlite3"
require "json"
require "uri"
require "net/http"
require "ipaddr"
require "socket"
require "cgi"
require "securerandom"
require "time"

# SQLite-backed link storage. A single connection is shared between threads
# and guarded by a mutex.
class LinkStore
  def initialize(path)
    @db = SQLite3::Database.new(path)
    @db.results_as_hash = true
    @lock = Mutex.new
    @db.execute <<~SQL
      CREATE TABLE IF NOT EXISTS links (
        code       TEXT PRIMARY KEY,
        url        TEXT NOT NULL,
        title      TEXT,
        created_at TEXT NOT NULL,
        clicks     INTEGER NOT NULL DEFAULT 0
      )
    SQL
  end

  # Returns true if inserted, false if the code is already taken.
  def insert(code:, url:, title:)
    @lock.synchronize do
      @db.execute(
        "INSERT INTO links (code, url, title, created_at) VALUES (?, ?, ?, ?)",
        [code, url, title, Time.now.utc.iso8601]
      )
      true
    end
  rescue SQLite3::ConstraintException
    false
  end

  def find(code)
    @lock.synchronize { @db.get_first_row("SELECT * FROM links WHERE code = ?", [code]) }
  end

  def exists?(code)
    !find(code).nil?
  end

  # Atomically bumps the click counter and returns the target URL (or nil).
  def record_click(code)
    @lock.synchronize do
      @db.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", [code])
      return nil if @db.changes.zero?

      @db.get_first_value("SELECT url FROM links WHERE code = ?", [code])
    end
  end
end

# Fetches the <title> of a page. Never raises: returns nil on any failure.
# Refuses to connect to loopback/private/link-local addresses (SSRF guard) and
# connects to the address it validated, so DNS can't change between check and use.
class TitleFetcher
  MAX_BYTES = 256 * 1024
  MAX_REDIRECTS = 3
  TIMEOUT = 5

  def self.extract_title(html)
    match = html.to_s.match(%r{<title[^>]*>(.*?)</title>}mi)
    return nil unless match

    title = CGI.unescapeHTML(match[1]).gsub(/\s+/, " ").strip
    title.empty? ? nil : title[0, 500]
  end

  def self.blocked_address?(ip)
    addr = IPAddr.new(ip.to_s)
    addr = addr.native if addr.ipv6? && addr.ipv4_mapped?
    addr.loopback? || addr.private? || addr.link_local? || addr.to_s == "0.0.0.0" || addr.to_s == "::"
  rescue IPAddr::Error
    true
  end

  def call(url, redirects_left = MAX_REDIRECTS)
    uri = URI.parse(url)
    return nil unless uri.is_a?(URI::HTTP) && uri.host

    ip = Addrinfo.getaddrinfo(uri.host, uri.port, nil, :STREAM).map(&:ip_address).first
    return nil if ip.nil? || self.class.blocked_address?(ip)

    http = Net::HTTP.new(uri.host, uri.port)
    http.ipaddr = ip
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = http.read_timeout = TIMEOUT

    http.request_get(uri.request_uri, "User-Agent" => "UrlShortener/1.0", "Accept" => "text/html") do |res|
      case res
      when Net::HTTPRedirection
        return nil if redirects_left.zero? || res["location"].nil?

        return call(URI.join(url, res["location"]).to_s, redirects_left - 1)
      when Net::HTTPSuccess
        body = +""
        res.read_body do |chunk|
          body << chunk
          break if body.bytesize >= MAX_BYTES || body =~ %r{</title>}i
        end
        body = body.force_encoding(Encoding::UTF_8).scrub
        return self.class.extract_title(body)
      end
    end
  rescue StandardError
    nil
  end
end

class UrlShortener < Sinatra::Base
  ALIAS_FORMAT = /\A[A-Za-z0-9_-]{3,32}\z/
  RESERVED = %w[api].freeze
  CODE_LENGTH = 7
  MAX_URL_LENGTH = 2048

  set :store, nil
  set :title_fetcher, TitleFetcher.new
  set :show_exceptions, false

  configure do
    set :store, LinkStore.new(ENV.fetch("DATABASE_PATH", "shortener.db"))
  end

  # POST /api/links  {"url": "...", "alias": "optional"}
  post "/api/links" do
    payload = parse_json_object
    url = validate_url(payload["url"])
    custom = payload["alias"]

    if custom.nil?
      code = nil
    else
      halt_error 422, "alias must be 3-32 characters: letters, digits, '-' or '_'" unless custom.is_a?(String) && custom.match?(ALIAS_FORMAT)
      halt_error 422, "alias is reserved" if RESERVED.include?(custom.downcase)
      halt_error 409, "alias already in use" if settings.store.exists?(custom)
      code = custom
    end

    title = settings.title_fetcher.call(url)

    if code
      halt_error 409, "alias already in use" unless settings.store.insert(code: code, url: url, title: title)
    else
      code = insert_with_random_code(url, title)
    end

    status 201
    json_response(link_json(settings.store.find(code)).merge("short_url" => "#{request.base_url}/#{code}"))
  end

  # GET /api/links/:code/stats
  get "/api/links/:code/stats" do
    link = settings.store.find(params[:code]) or halt_error(404, "not found")
    json_response(link_json(link))
  end

  # GET /:code -> 302 to the long URL, counting the click
  get %r{/([A-Za-z0-9_-]+)} do |code|
    url = settings.store.record_click(code) or halt_error(404, "not found")
    redirect url, 302
  end

  not_found do
    content_type :json
    { error: "not found" }.to_json
  end

  private

  def insert_with_random_code(url, title)
    5.times do
      code = SecureRandom.alphanumeric(CODE_LENGTH)
      return code if settings.store.insert(code: code, url: url, title: title)
    end
    halt_error 500, "could not generate a unique code"
  end

  def parse_json_object
    data = JSON.parse(request.body.read)
    halt_error 400, "body must be a JSON object" unless data.is_a?(Hash)
    data
  rescue JSON::ParserError
    halt_error 400, "invalid JSON"
  end

  def validate_url(url)
    halt_error 422, "url is required" unless url.is_a?(String) && !url.empty?
    halt_error 422, "url is too long" if url.length > MAX_URL_LENGTH

    uri = begin
      URI.parse(url)
    rescue URI::InvalidURIError
      nil
    end
    halt_error 422, "url must be a valid http or https URL" unless uri.is_a?(URI::HTTP) && uri.host && !uri.host.empty?
    url
  end

  def link_json(link)
    {
      "code" => link["code"],
      "url" => link["url"],
      "title" => link["title"],
      "clicks" => link["clicks"],
      "created_at" => link["created_at"]
    }
  end

  def json_response(hash)
    content_type :json
    hash.to_json
  end

  def halt_error(code, message)
    halt code, { "Content-Type" => "application/json" }, { error: message }.to_json
  end

  run! if app_file == $PROGRAM_NAME
end
