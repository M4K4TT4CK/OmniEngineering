ENV["APP_ENV"] = "test"

require "minitest/autorun"
require "rack/test"
require "tmpdir"
require "base64"
require "time"
require_relative "app"

class AppTest < Minitest::Test
  include Rack::Test::Methods

  # 1x1 transparent PNG
  PNG = Base64.decode64(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
  )

  def app = App

  def setup
    @dir = Dir.mktmpdir("uploads")
    App.set :upload_dir, @dir
    App.set :max_upload_bytes, 1024 * 1024
  end

  def teardown
    FileUtils.remove_entry(@dir)
  end

  def upload(name, content, type = "application/octet-stream")
    io = Rack::Test::UploadedFile.new(StringIO.new(content), type, original_filename: name)
    post "/files", file: io
  end

  def json = JSON.parse(last_response.body)

  def test_upload_and_list
    upload("hello.txt", "hello world", "text/plain")
    assert_equal 201, last_response.status
    assert_equal 11, json["size"]

    get "/files"
    assert_equal 200, last_response.status
    assert_equal ["hello.txt"], json.map { |f| f["name"] }
    assert_equal 11, json.first["size"]
    assert Time.iso8601(json.first["uploaded_at"])
  end

  def test_download
    upload("a.txt", "abc")
    get "/files/a.txt"
    assert_equal 200, last_response.status
    assert_equal "abc", last_response.body
    assert_match(/attachment/, last_response.headers["Content-Disposition"])
  end

  def test_download_missing
    get "/files/nope.txt"
    assert_equal 404, last_response.status
  end

  def test_duplicate_upload_conflicts
    upload("a.txt", "one")
    upload("a.txt", "two")
    assert_equal 409, last_response.status
    get "/files/a.txt"
    assert_equal "one", last_response.body
  end

  def test_missing_file_field
    post "/files", other: "x"
    assert_equal 400, last_response.status
  end

  def test_delete
    upload("a.txt", "abc")
    delete "/files/a.txt"
    assert_equal 204, last_response.status
    get "/files/a.txt"
    assert_equal 404, last_response.status
    delete "/files/a.txt"
    assert_equal 404, last_response.status
  end

  def test_path_traversal_in_upload_name_is_stripped
    upload("../../evil.txt", "x")
    assert_equal 201, last_response.status
    assert_equal "evil.txt", json["name"]
    assert File.file?(File.join(@dir, "evil.txt"))
    refute File.exist?(File.expand_path("../evil.txt", @dir))
  end

  def test_invalid_names_rejected
    upload(".hidden", "x")
    assert_equal 400, last_response.status
    get "/files/%2e%2e"
    assert_includes [400, 404], last_response.status
    get "/files/..%2Fsecret"
    assert_includes [400, 404], last_response.status
  end

  def test_too_large
    App.set :max_upload_bytes, 5
    upload("big.bin", "123456")
    assert_equal 413, last_response.status
    refute File.exist?(File.join(@dir, "big.bin"))
  end

  def test_thumbnail_rejects_non_image
    upload("a.txt", "abc")
    post "/files/a.txt/thumbnail"
    assert_equal 415, last_response.status
  end

  def test_thumbnail_missing_file
    post "/files/none.png/thumbnail"
    assert_equal 404, last_response.status
  end

  def test_thumbnail_not_generated_yet
    upload("p.png", PNG)
    get "/files/p.png/thumbnail"
    assert_equal 404, last_response.status
  end

  def test_thumbnail_lifecycle
    skip "ImageMagick not installed" unless imagemagick?

    upload("p.png", PNG)
    post "/files/p.png/thumbnail"
    assert_equal 201, last_response.status

    get "/files/p.png/thumbnail"
    assert_equal 200, last_response.status
    assert_equal "image/png", last_response.content_type
    assert last_response.body.start_with?(PNG[0, 4])

    get "/files"
    assert_equal ["p.png"], json.map { |f| f["name"] } # thumbnail not listed as a file
    assert_equal true, json.first["thumbnail"]

    delete "/files/p.png"
    refute File.exist?(File.join(@dir, ".thumbnails", "p.png.png"))
  end

  private

  def imagemagick?
    MiniMagick.cli_prefix # forces CLI detection
    MiniMagick.imagemagick7? || MiniMagick.imagemagick?
  rescue StandardError
    false
  end
end
