ENV["RACK_ENV"] = "test"

require "minitest/autorun"
require "rack/test"
require "tmpdir"
require "socket"
require_relative "app"

class FakeFetcher
  attr_reader :urls

  def initialize(data)
    @data = data
    @urls = []
  end

  def fetch(url)
    @urls << url
    @data
  end
end

module ImageHelpers
  def png(width = 400, height = 200)
    Vips::Image.black(width, height).write_to_buffer(".png")
  end

  def jpeg(width = 400, height = 200)
    Vips::Image.black(width, height).write_to_buffer(".jpg")
  end
end

class ThumbnailServiceTest < Minitest::Test
  include Rack::Test::Methods
  include ImageHelpers

  def app
    ThumbnailService
  end

  def setup
    @cache_dir = Dir.mktmpdir("thumbs")
    @tmp_dir = Dir.mktmpdir("uploads")
    ThumbnailService.set :cache_dir, @cache_dir
    ThumbnailService.set :fetcher, nil
    ThumbnailService.set :public_base_url, nil
  end

  def teardown
    FileUtils.rm_rf(@cache_dir)
    FileUtils.rm_rf(@tmp_dir)
  end

  def upload(data, name = "image.png")
    path = File.join(@tmp_dir, name)
    File.binwrite(path, data)
    Rack::Test::UploadedFile.new(path, "application/octet-stream", true)
  end

  def body
    JSON.parse(last_response.body)
  end

  def path_of(url)
    URI(url).path
  end

  def test_upload_returns_thumbnail_urls_that_serve_resized_images
    post "/thumbnails", image: upload(png), widths: "100,250"

    assert_equal 200, last_response.status
    thumbs = body["thumbnails"]
    assert_equal [100, 250], thumbs.map { |t| t["width"] }
    assert_match %r{\Ahttp://example.org/thumbnails/\h{64}/100\.png\z}, thumbs[0]["url"]

    get path_of(thumbs[0]["url"])
    assert_equal 200, last_response.status
    assert_equal "image/png", last_response.content_type
    assert_includes last_response.headers["Cache-Control"], "immutable"
    image = Vips::Image.new_from_buffer(last_response.body, "")
    assert_equal [100, 50], [image.width, image.height]
  end

  def test_widths_as_array_param
    post "/thumbnails", image: upload(jpeg, "a.jpg"), widths: %w[64 32]

    assert_equal 200, last_response.status
    assert_equal [64, 32], body["thumbnails"].map { |t| t["width"] }
    assert body["thumbnails"].all? { |t| t["url"].end_with?(".jpg") }
  end

  def test_url_source_via_json
    fetcher = FakeFetcher.new(png)
    ThumbnailService.set :fetcher, fetcher

    post "/thumbnails", JSON.generate(url: "https://images.example.com/cat.png", widths: [120]),
         "CONTENT_TYPE" => "application/json"

    assert_equal 200, last_response.status
    assert_equal ["https://images.example.com/cat.png"], fetcher.urls
    get path_of(body["thumbnails"][0]["url"])
    assert_equal 120, Vips::Image.new_from_buffer(last_response.body, "").width
  end

  def test_thumbnails_are_cached_and_not_regenerated
    data = png
    post "/thumbnails", image: upload(data), widths: "80"
    file = File.join(@cache_dir, Digest::SHA256.hexdigest(data), "80.png")
    assert File.file?(file)
    old = Time.at(1_000_000_000)
    File.utime(old, old, file)

    post "/thumbnails", image: upload(data), widths: "80"

    assert_equal 200, last_response.status
    assert_equal old, File.mtime(file)
    assert_empty Dir.glob(File.join(@cache_dir, "**", "*.tmp.*"), File::FNM_DOTMATCH)
  end

  def test_same_image_from_upload_and_url_share_cache_entry
    data = png
    post "/thumbnails", image: upload(data), widths: "50"
    first = body["thumbnails"][0]["url"]

    ThumbnailService.set :fetcher, FakeFetcher.new(data)
    post "/thumbnails", url: "https://example.com/x.png", widths: "50"

    assert_equal first, body["thumbnails"][0]["url"]
  end

  def test_does_not_upscale
    post "/thumbnails", image: upload(png(40, 20)), widths: "400"

    get path_of(body["thumbnails"][0]["url"])
    assert_equal 40, Vips::Image.new_from_buffer(last_response.body, "").width
  end

  def test_public_base_url_overrides_host_header
    ThumbnailService.set :public_base_url, "https://cdn.example.net/"
    post "/thumbnails", { image: upload(png), widths: "10" }, "HTTP_HOST" => "evil.test"

    assert body["thumbnails"][0]["url"].start_with?("https://cdn.example.net/thumbnails/")
  end

  def test_metadata_is_stripped
    tagged = Vips::Image.black(100, 100).mutate { |m| m.set_type!(GObject::GSTR_TYPE, "exif-ifd0-Artist", "secret") }
    post "/thumbnails", image: upload(tagged.write_to_buffer(".jpg"), "t.jpg"), widths: "50"

    get path_of(body["thumbnails"][0]["url"])
    refute_includes last_response.body.b, "secret".b
  end

  def test_invalid_widths_are_rejected
    ["", "0", "abc", "10.5", "-5", "5000", (1..11).to_a.join(",")].each do |widths|
      post "/thumbnails", image: upload(png), widths: widths
      assert_equal 400, last_response.status, "widths=#{widths.inspect}"
      assert body["error"]
    end
  end

  def test_missing_widths_is_rejected
    post "/thumbnails", image: upload(png)
    assert_equal 400, last_response.status
  end

  def test_missing_source_is_rejected
    post "/thumbnails", widths: "100"
    assert_equal 400, last_response.status
  end

  def test_both_sources_is_rejected
    post "/thumbnails", image: upload(png), url: "https://example.com/a.png", widths: "100"
    assert_equal 400, last_response.status
  end

  def test_non_image_is_rejected
    post "/thumbnails", image: upload("<svg xmlns='http://www.w3.org/2000/svg'/>", "x.svg"), widths: "100"
    assert_equal 415, last_response.status
  end

  def test_corrupt_image_is_rejected
    post "/thumbnails", image: upload("\x89PNG\r\n\x1A\ngarbage".b), widths: "100"
    assert_equal 422, last_response.status
  end

  def test_oversized_dimensions_are_rejected
    ThumbnailService.set :max_pixels, 1_000
    post "/thumbnails", image: upload(png(100, 100)), widths: "10"
    assert_equal 413, last_response.status
  ensure
    ThumbnailService.set :max_pixels, 50_000_000
  end

  def test_oversized_upload_is_rejected
    ThumbnailService.set :max_image_bytes, 100
    post "/thumbnails", image: upload(png), widths: "10"
    assert_equal 413, last_response.status
  ensure
    ThumbnailService.set :max_image_bytes, 10 * 1024 * 1024
  end

  def test_invalid_json_is_rejected
    post "/thumbnails", "{nope", "CONTENT_TYPE" => "application/json"
    assert_equal 400, last_response.status
  end

  def test_unknown_thumbnail_is_404
    get "/thumbnails/#{"a" * 64}/100.png"
    assert_equal 404, last_response.status
  end

  def test_paths_outside_the_cache_are_not_served
    ["/thumbnails/../app.rb", "/thumbnails/%2e%2e/%2e%2e/etc/passwd", "/thumbnails/#{"a" * 64}/100.rb"].each do |path|
      get path
      assert_equal 404, last_response.status, path
    end
  end
end

class FetcherTest < Minitest::Test
  include ImageHelpers

  def fetcher(**opts)
    Thumbnailer::Fetcher.new(max_bytes: 10_000, **opts)
  end

  def assert_fetch_error(url, status, **opts)
    error = assert_raises(Thumbnailer::Error) { fetcher(**opts).fetch(url) }
    assert_equal status, error.http_status, error.message
  end

  def test_rejects_non_http_schemes
    ["file:///etc/passwd", "ftp://example.com/a.png", "gopher://x/", "not a url", "/relative.png"].each do |url|
      assert_fetch_error(url, 400)
    end
  end

  def test_rejects_private_and_loopback_addresses
    %w[
      http://127.0.0.1/a.png http://localhost/a.png http://10.1.2.3/ http://192.168.0.1/
      http://172.16.5.5/ http://169.254.169.254/latest/meta-data http://0.0.0.0/
      http://[::1]/ http://[::ffff:127.0.0.1]/ http://[fd00::1]/ http://[fe80::1]/
    ].each do |url|
      assert_fetch_error(url, 400)
    end
  end

  def test_blocked_ranges
    assert Thumbnailer::Fetcher.blocked?(IPAddr.new("100.64.0.1"))
    assert Thumbnailer::Fetcher.blocked?(IPAddr.new("::ffff:10.0.0.1"))
    refute Thumbnailer::Fetcher.blocked?(IPAddr.new("93.184.216.34"))
    refute Thumbnailer::Fetcher.blocked?(IPAddr.new("2606:4700::1111"))
  end

  # Minimal HTTP server that replies to each request with the next canned response.
  def serve(*responses)
    server = TCPServer.new("127.0.0.1", 0)
    thread = Thread.new do
      responses.each do |response|
        client = server.accept
        while (line = client.gets) && line != "\r\n"; end
        client.write(response.call("http://127.0.0.1:#{server.addr[1]}"))
        client.close
      end
    rescue StandardError
      nil # client hung up early, e.g. after rejecting an oversized body
    end
    yield "http://127.0.0.1:#{server.addr[1]}"
  ensure
    server&.close
    thread&.kill
  end

  def ok(body, length: true)
    lambda do |_|
      head = "HTTP/1.1 200 OK\r\nConnection: close\r\n"
      head += "Content-Length: #{body.bytesize}\r\n" if length
      "#{head}\r\n".b + body.b
    end
  end

  def redirect(path)
    ->(_) { "HTTP/1.1 302 Found\r\nLocation: #{path}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n" }
  end

  def test_downloads_and_follows_redirects
    data = png(10, 10)
    serve(redirect("/real.png"), ok(data)) do |base|
      assert_equal data, fetcher(allow_private: true).fetch("#{base}/start")
    end
  end

  def test_rejects_too_many_redirects
    serve(*Array.new(4) { redirect("/again") }) do |base|
      assert_fetch_error("#{base}/start", 422, allow_private: true)
    end
  end

  def test_rejects_oversized_body_by_content_length
    serve(ok("x" * 20_000)) do |base|
      assert_fetch_error("#{base}/big", 413, allow_private: true)
    end
  end

  def test_rejects_oversized_body_without_content_length
    serve(ok("x" * 20_000, length: false)) do |base|
      assert_fetch_error("#{base}/big", 413, allow_private: true)
    end
  end

  def test_rejects_error_responses
    serve(->(_) { "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n" }) do |base|
      assert_fetch_error("#{base}/missing", 422, allow_private: true)
    end
  end
end
