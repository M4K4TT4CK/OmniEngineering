# frozen_string_literal: true

require "json"
require "net/http"
require "securerandom"
require "sinatra/base"
require "sqlite3"
require "time"
require "uri"

# SQLite-backed store for users who have signed in with GitHub.
class UserStore
  def initialize(path)
    @lock = Mutex.new
    @db = SQLite3::Database.new(path)
    @db.results_as_hash = true
    @db.busy_timeout = 5000
    @db.execute(<<~SQL)
      CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        github_id  INTEGER NOT NULL UNIQUE,
        login      TEXT    NOT NULL,
        name       TEXT,
        email      TEXT,
        avatar_url TEXT,
        html_url   TEXT,
        created_at TEXT    NOT NULL,
        updated_at TEXT    NOT NULL
      )
    SQL
  end

  # Creates the user on first sign-in, refreshes their profile fields afterwards.
  def upsert_from_github(profile)
    now = Time.now.utc.iso8601
    values = [
      profile["id"], profile["login"], profile["name"], profile["email"],
      profile["avatar_url"], profile["html_url"], now, now
    ]
    @lock.synchronize do
      @db.execute(<<~SQL, values)
        INSERT INTO users (github_id, login, name, email, avatar_url, html_url, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(github_id) DO UPDATE SET
          login      = excluded.login,
          name       = excluded.name,
          email      = excluded.email,
          avatar_url = excluded.avatar_url,
          html_url   = excluded.html_url,
          updated_at = excluded.updated_at
      SQL
      @db.get_first_row("SELECT * FROM users WHERE github_id = ?", [profile["id"]])
    end
  end

  def find(id)
    @lock.synchronize { @db.get_first_row("SELECT * FROM users WHERE id = ?", [id]) }
  end

  def count
    @lock.synchronize { @db.get_first_value("SELECT COUNT(*) FROM users") }
  end
end

class App < Sinatra::Base
  class OAuthError < StandardError; end

  GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
  GITHUB_TOKEN_URL = URI("https://github.com/login/oauth/access_token")
  GITHUB_USER_URL = URI("https://api.github.com/user")

  configure do
    secret = ENV["SESSION_SECRET"].to_s
    if secret.bytesize < 64
      raise "SESSION_SECRET must be set to at least 64 characters" if production?

      secret = SecureRandom.hex(64)
    end

    if production? && (ENV["GITHUB_CLIENT_ID"].to_s.empty? || ENV["GITHUB_CLIENT_SECRET"].to_s.empty?)
      raise "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set"
    end

    enable :logging
    set :session_secret, secret
    set :sessions, key: "oauth_login.session", httponly: true, same_site: :lax,
                   secure: production?, expire_after: 8 * 60 * 60
    set :github_client_id, ENV.fetch("GITHUB_CLIENT_ID", "")
    set :github_client_secret, ENV.fetch("GITHUB_CLIENT_SECRET", "")
    set :github_redirect_uri, ENV.fetch("GITHUB_REDIRECT_URI", "http://localhost:9292/auth/github/callback")
    set :users, UserStore.new(ENV.fetch("DATABASE_PATH", "users.db"))
  end

  helpers do
    def h(text)
      Rack::Utils.escape_html(text.to_s)
    end

    # Only http(s) URLs are rendered as links/images, so profile data can't inject javascript: URLs.
    def safe_url(value)
      uri = URI.parse(value.to_s)
      %w[http https].include?(uri.scheme) ? uri.to_s : nil
    rescue URI::InvalidURIError
      nil
    end

    def current_user
      return @current_user if defined?(@current_user)

      @current_user = session[:user_id] && settings.users.find(session[:user_id])
      session.clear if session[:user_id] && @current_user.nil?
      @current_user
    end

    def csrf_token
      session[:csrf_token] ||= SecureRandom.urlsafe_base64(32)
    end
  end

  get "/" do
    erb :home
  end

  get "/login" do
    state = SecureRandom.urlsafe_base64(32)
    session[:oauth_state] = state
    session[:return_to] = safe_return_to(params["return_to"] || same_origin_referer)

    query = URI.encode_www_form(
      client_id: settings.github_client_id,
      redirect_uri: settings.github_redirect_uri,
      scope: "read:user user:email",
      state: state
    )
    redirect "#{GITHUB_AUTHORIZE_URL}?#{query}"
  end

  get "/auth/github/callback" do
    # The state is single-use: remove it before anything else can fail.
    expected_state = session.delete(:oauth_state)
    return_to = session.delete(:return_to)

    unless expected_state && Rack::Utils.secure_compare(expected_state, params["state"].to_s)
      halt 400, "Invalid or expired login attempt. Please try signing in again."
    end
    halt 400, "GitHub sign-in was cancelled or denied." if params["error"]

    code = params["code"].to_s
    halt 400, "Missing authorization code." if code.empty?

    begin
      profile = fetch_profile(exchange_code(code))
    rescue OAuthError => e
      logger.warn("GitHub OAuth failed: #{e.message}")
      halt 502, "Could not complete sign-in with GitHub. Please try again."
    end

    user = settings.users.upsert_from_github(profile)

    # Start a fresh session so no pre-login session data (or id) survives.
    session.clear
    env["rack.session.options"][:renew] = true
    session[:user_id] = user["id"]
    session[:csrf_token] = SecureRandom.urlsafe_base64(32)

    redirect safe_return_to(return_to)
  end

  get "/profile" do
    redirect "/login?#{URI.encode_www_form(return_to: '/profile')}" unless current_user

    @user = current_user
    erb :profile
  end

  post "/logout" do
    token = params["csrf_token"].to_s
    halt 403, "Invalid CSRF token." unless session[:csrf_token] && Rack::Utils.secure_compare(session[:csrf_token], token)

    session.clear
    env["rack.session.options"][:renew] = true
    redirect "/"
  end

  private

  # Returns a local path to send the user back to, or "/" for anything that could leave the site.
  def safe_return_to(value)
    return "/" unless value.is_a?(String) && value.start_with?("/")
    return "/" if value.start_with?("//") || value.match?(/[\\\x00-\x1f\x7f]/)

    uri = URI.parse(value)
    return "/" if uri.scheme || uri.host
    return "/" if uri.path == "/login" || uri.path.start_with?("/auth/")

    value
  rescue URI::InvalidURIError
    "/"
  end

  def same_origin_referer
    return nil unless request.referer

    uri = URI.parse(request.referer)
    return nil unless uri.host == request.host && uri.port == request.port

    [uri.path, uri.query].compact.join("?")
  rescue URI::InvalidURIError
    nil
  end

  def exchange_code(code)
    request = Net::HTTP::Post.new(GITHUB_TOKEN_URL)
    request["Accept"] = "application/json"
    request.set_form_data(
      client_id: settings.github_client_id,
      client_secret: settings.github_client_secret,
      code: code,
      redirect_uri: settings.github_redirect_uri
    )
    body = github_json(GITHUB_TOKEN_URL, request)

    token = body["access_token"]
    raise OAuthError, "token exchange returned #{body['error'] || 'no access_token'}" unless token.is_a?(String) && !token.empty?

    token
  end

  def fetch_profile(token)
    request = Net::HTTP::Get.new(GITHUB_USER_URL)
    request["Accept"] = "application/vnd.github+json"
    request["Authorization"] = "Bearer #{token}"
    profile = github_json(GITHUB_USER_URL, request)

    raise OAuthError, "unexpected profile payload" unless profile["id"].is_a?(Integer) && profile["login"].is_a?(String)

    profile
  end

  def github_json(uri, request)
    request["User-Agent"] = "oauth-login-example"
    response = Net::HTTP.start(uri.host, uri.port, use_ssl: true, open_timeout: 5, read_timeout: 10) do |http|
      http.request(request)
    end
    raise OAuthError, "#{uri.host} responded with HTTP #{response.code}" unless response.is_a?(Net::HTTPSuccess)

    body = JSON.parse(response.body.to_s)
    raise OAuthError, "#{uri.host} returned a non-object JSON body" unless body.is_a?(Hash)

    body
  rescue JSON::ParserError, SocketError, SystemCallError, Timeout::Error, OpenSSL::SSL::SSLError, Net::HTTPBadResponse => e
    raise OAuthError, "request to #{uri.host} failed (#{e.class})"
  end
end

__END__

@@ layout
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>GitHub Login</title>
  </head>
  <body>
    <nav>
      <a href="/">Home</a>
      <% if current_user %>
        | <a href="/profile">Profile</a>
      <% end %>
    </nav>
    <main><%= yield %></main>
  </body>
</html>

@@ home
<h1>Welcome</h1>
<% if current_user %>
  <p>Signed in as <strong><%= h current_user["login"] %></strong>.</p>
<% else %>
  <p><a href="/login?return_to=%2F">Sign in with GitHub</a></p>
<% end %>

@@ profile
<h1>Your profile</h1>
<% if (avatar = safe_url(@user["avatar_url"])) %>
  <img src="<%= h avatar %>" alt="Avatar" width="96" height="96">
<% end %>
<dl>
  <dt>Username</dt><dd><%= h @user["login"] %></dd>
  <dt>Name</dt><dd><%= h(@user["name"] || "—") %></dd>
  <dt>Email</dt><dd><%= h(@user["email"] || "—") %></dd>
  <dt>GitHub ID</dt><dd><%= h @user["github_id"] %></dd>
  <% if (link = safe_url(@user["html_url"])) %>
    <dt>GitHub page</dt><dd><a href="<%= h link %>" rel="noopener noreferrer"><%= h link %></a></dd>
  <% end %>
  <dt>First signed in</dt><dd><%= h @user["created_at"] %></dd>
  <dt>Last updated</dt><dd><%= h @user["updated_at"] %></dd>
</dl>
<form action="/logout" method="post">
  <input type="hidden" name="csrf_token" value="<%= h csrf_token %>">
  <button type="submit">Log out</button>
</form>
