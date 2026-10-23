# frozen_string_literal: true

ENV["APP_ENV"] = "test"
ENV["DATABASE_PATH"] = ":memory:"
ENV["GITHUB_CLIENT_ID"] = "test-client-id"
ENV["GITHUB_CLIENT_SECRET"] = "test-client-secret"
ENV["GITHUB_REDIRECT_URI"] = "http://example.org/auth/github/callback"

require "minitest/autorun"
require "rack/test"
require "webmock/minitest"
require_relative "app"

class AppTest < Minitest::Test
  include Rack::Test::Methods

  TOKEN_URL = "https://github.com/login/oauth/access_token"
  USER_URL = "https://api.github.com/user"

  def app
    App
  end

  def setup
    App.set :users, UserStore.new(":memory:")
  end

  def profile(overrides = {})
    {
      "id" => 4242,
      "login" => "octocat",
      "name" => "The Octocat",
      "email" => "octocat@example.com",
      "avatar_url" => "https://avatars.githubusercontent.com/u/4242",
      "html_url" => "https://github.com/octocat"
    }.merge(overrides)
  end

  def stub_github(code: "good-code", token: "gho_token", user: profile)
    stub_request(:post, TOKEN_URL)
      .with(body: hash_including("code" => code, "client_id" => "test-client-id",
                                 "client_secret" => "test-client-secret"))
      .to_return(status: 200, body: { access_token: token, token_type: "bearer" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:get, USER_URL)
      .with(headers: { "Authorization" => "Bearer #{token}" })
      .to_return(status: 200, body: user.to_json, headers: { "Content-Type" => "application/json" })
  end

  def start_login(params = {}, headers = {})
    get "/login", params, headers
    Rack::Utils.parse_query(URI(last_response.location).query)["state"]
  end

  def sign_in(return_to: "/profile", user: profile)
    stub_github(user: user)
    state = start_login("return_to" => return_to)
    get "/auth/github/callback", "code" => "good-code", "state" => state
  end

  def redirect_target
    uri = URI(last_response.location)
    assert_includes [nil, "example.org"], uri.host
    [uri.path, uri.query].compact.join("?")
  end

  def csrf_token_from_page
    last_response.body[/name="csrf_token" value="([^"]+)"/, 1]
  end

  def test_login_redirects_to_github_with_state
    get "/login"

    assert_equal 302, last_response.status
    uri = URI(last_response.location)
    assert_equal "github.com", uri.host
    assert_equal "/login/oauth/authorize", uri.path
    query = Rack::Utils.parse_query(uri.query)
    assert_equal "test-client-id", query["client_id"]
    assert_equal "http://example.org/auth/github/callback", query["redirect_uri"]
    assert_operator query["state"].length, :>=, 32
  end

  def test_login_uses_a_new_state_each_time
    refute_equal start_login, start_login
  end

  def test_callback_creates_user_starts_session_and_returns_to_original_page
    sign_in(return_to: "/profile?tab=details")

    assert_equal 302, last_response.status
    assert_equal "/profile?tab=details", redirect_target
    assert_equal 1, App.users.count

    get "/profile"
    assert_equal 200, last_response.status
    assert_includes last_response.body, "octocat"
    assert_includes last_response.body, "The Octocat"
    assert_includes last_response.body, "octocat@example.com"
    assert_includes last_response.body, "https://github.com/octocat"
  end

  def test_return_to_falls_back_to_same_origin_referer
    stub_github
    state = start_login({}, "HTTP_REFERER" => "http://example.org/somewhere?x=1")
    get "/auth/github/callback", "code" => "good-code", "state" => state

    assert_equal "/somewhere?x=1", redirect_target
  end

  def test_foreign_referer_is_ignored
    stub_github
    state = start_login({}, "HTTP_REFERER" => "https://evil.example/phish")
    get "/auth/github/callback", "code" => "good-code", "state" => state

    assert_equal "/", redirect_target
  end

  def test_return_to_rejects_offsite_targets
    ["//evil.example", "https://evil.example/", "/\\evil.example", "evil", "/login"].each do |target|
      clear_cookies
      sign_in(return_to: target)
      assert_equal "/", redirect_target, "return_to #{target.inspect} should not be honoured"
    end
  end

  def test_second_login_updates_existing_user
    sign_in
    clear_cookies
    WebMock.reset!
    sign_in(user: profile("name" => "Renamed Cat", "login" => "octocat2"))

    assert_equal 1, App.users.count
    get "/profile"
    assert_includes last_response.body, "Renamed Cat"
    assert_includes last_response.body, "octocat2"
  end

  def test_callback_rejects_wrong_state
    stub_github
    start_login
    get "/auth/github/callback", "code" => "good-code", "state" => "forged"

    assert_equal 400, last_response.status
    assert_not_requested :post, TOKEN_URL
    assert_equal 0, App.users.count
  end

  def test_callback_rejects_missing_session_state
    stub_github
    get "/auth/github/callback", "code" => "good-code", "state" => "anything"

    assert_equal 400, last_response.status
    assert_not_requested :post, TOKEN_URL
  end

  def test_state_is_single_use
    stub_github
    state = start_login
    get "/auth/github/callback", "code" => "good-code", "state" => state
    assert_equal 302, last_response.status

    get "/auth/github/callback", "code" => "good-code", "state" => state
    assert_equal 400, last_response.status
  end

  def test_callback_handles_denied_authorization
    state = start_login
    get "/auth/github/callback", "error" => "access_denied", "state" => state

    assert_equal 400, last_response.status
    assert_equal 0, App.users.count
  end

  def test_callback_handles_token_exchange_error
    stub_request(:post, TOKEN_URL)
      .to_return(status: 200, body: { error: "bad_verification_code" }.to_json)
    state = start_login
    get "/auth/github/callback", "code" => "bad-code", "state" => state

    assert_equal 502, last_response.status
    assert_not_requested :get, USER_URL
    assert_equal 0, App.users.count
  end

  def test_callback_handles_profile_fetch_failure
    stub_request(:post, TOKEN_URL).to_return(status: 200, body: { access_token: "gho_token" }.to_json)
    stub_request(:get, USER_URL).to_return(status: 401, body: { message: "Bad credentials" }.to_json)
    state = start_login
    get "/auth/github/callback", "code" => "good-code", "state" => state

    assert_equal 502, last_response.status
    assert_equal 0, App.users.count
  end

  def test_callback_handles_network_timeout
    stub_request(:post, TOKEN_URL).to_timeout
    state = start_login
    get "/auth/github/callback", "code" => "good-code", "state" => state

    assert_equal 502, last_response.status
  end

  def test_profile_requires_login
    get "/profile"

    assert_equal 302, last_response.status
    assert_equal "/login?return_to=%2Fprofile", redirect_target
  end

  def test_profile_escapes_user_data
    sign_in(user: profile("name" => "<script>alert(1)</script>", "html_url" => "javascript:alert(1)"))
    get "/profile"

    assert_includes last_response.body, "&lt;script&gt;alert(1)"
    refute_includes last_response.body, "<script>alert(1)</script>"
    refute_includes last_response.body, "javascript:"
  end

  def test_logout_ends_session
    sign_in
    get "/profile"
    token = csrf_token_from_page
    refute_nil token

    post "/logout", "csrf_token" => token
    assert_equal 302, last_response.status
    assert_equal "/", redirect_target

    get "/profile"
    assert_equal 302, last_response.status
  end

  def test_logout_requires_csrf_token
    sign_in
    post "/logout", "csrf_token" => "wrong"
    assert_equal 403, last_response.status

    get "/profile"
    assert_equal 200, last_response.status
  end
end
