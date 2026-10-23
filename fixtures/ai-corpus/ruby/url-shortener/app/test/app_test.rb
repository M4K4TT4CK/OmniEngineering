ENV["APP_ENV"] = "test"
ENV["DATABASE_PATH"] = ":memory:"

require "minitest/autorun"
require "rack/test"
require_relative "../app"

class UrlShortenerTest < Minitest::Test
  include Rack::Test::Methods

  def setup
    @fetched = []
    fetched = @fetched
    store = LinkStore.new(":memory:")
    fetcher = lambda do |url|
      fetched << url
      "Title of #{url}"
    end
    # Fresh subclass per test so each test gets its own in-memory database.
    @app = Class.new(UrlShortener) do
      set :store, store
      set :title_fetcher, fetcher
    end
  end

  def app
    @app
  end

  def create(body)
    post "/api/links", body.to_json, "CONTENT_TYPE" => "application/json"
    last_response
  end

  def json
    JSON.parse(last_response.body)
  end

  def test_create_returns_code_and_title
    res = create(url: "https://example.com/page")
    assert_equal 201, res.status
    assert_match(/\A[A-Za-z0-9]{7}\z/, json["code"])
    assert_equal "http://example.org/#{json['code']}", json["short_url"]
    assert_equal "Title of https://example.com/page", json["title"]
    assert_equal 0, json["clicks"]
  end

  def test_create_with_custom_alias
    res = create(url: "https://example.com", alias: "my-link")
    assert_equal 201, res.status
    assert_equal "my-link", json["code"]
  end

  def test_duplicate_alias_conflicts_without_fetching
    create(url: "https://example.com", alias: "taken")
    @fetched.clear
    res = create(url: "https://other.example.com", alias: "taken")
    assert_equal 409, res.status
    assert_empty @fetched
  end

  def test_invalid_alias_rejected
    ["ab", "has space", "bad/slash", "x" * 33, "api", 123].each do |bad|
      assert_equal 422, create(url: "https://example.com", alias: bad).status, "alias #{bad.inspect}"
    end
  end

  def test_invalid_urls_rejected
    ["", "not a url", "ftp://example.com", "javascript:alert(1)", "http://", nil, 5].each do |bad|
      assert_equal 422, create(url: bad).status, "url #{bad.inspect}"
    end
  end

  def test_malformed_json_rejected
    post "/api/links", "{nope", "CONTENT_TYPE" => "application/json"
    assert_equal 400, last_response.status
    assert_equal 400, create([1, 2]).status
  end

  def test_redirect_and_click_count
    create(url: "https://example.com/target?a=1", alias: "go")
    2.times do
      get "/go"
      assert_equal 302, last_response.status
      assert_equal "https://example.com/target?a=1", last_response.headers["Location"]
    end

    get "/api/links/go/stats"
    assert_equal 200, last_response.status
    assert_equal 2, json["clicks"]
  end

  def test_stats_fields
    create(url: "https://example.com", alias: "stat")
    get "/api/links/stat/stats"
    assert_equal %w[clicks code created_at title url], json.keys.sort
    assert_equal 0, json["clicks"]
    assert_equal "Title of https://example.com", json["title"]
    Time.iso8601(json["created_at"])
  end

  def test_stats_do_not_count_as_click
    create(url: "https://example.com", alias: "quiet")
    get "/api/links/quiet/stats"
    get "/api/links/quiet/stats"
    assert_equal 0, json["clicks"]
  end

  def test_unknown_code_is_404
    get "/missing"
    assert_equal 404, last_response.status
    get "/api/links/missing/stats"
    assert_equal 404, last_response.status
  end

  def test_link_created_even_if_title_unavailable
    @app.set :title_fetcher, ->(_url) { nil }
    res = create(url: "https://example.com")
    assert_equal 201, res.status
    assert_nil json["title"]
  end
end

class TitleFetcherTest < Minitest::Test
  def test_extract_title
    assert_equal "Hello & welcome", TitleFetcher.extract_title("<html><TITLE lang='en'>\n Hello &amp;\n welcome </TITLE>")
    assert_nil TitleFetcher.extract_title("<html></html>")
    assert_nil TitleFetcher.extract_title("<title>  </title>")
  end

  def test_blocks_internal_addresses
    %w[127.0.0.1 10.0.0.5 192.168.1.1 172.16.0.1 169.254.169.254 ::1 0.0.0.0 ::ffff:127.0.0.1].each do |ip|
      assert TitleFetcher.blocked_address?(ip), ip
    end
    refute TitleFetcher.blocked_address?("93.184.216.34")
  end

  def test_fetcher_refuses_localhost
    assert_nil TitleFetcher.new.call("http://127.0.0.1:9/")
  end
end
