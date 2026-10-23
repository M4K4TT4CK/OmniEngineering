# Keeps Sinatra's development-only host checks out of the way for rack-test requests.
ENV["RACK_ENV"] = "test"

require "fileutils"
require "json"
require "minitest/autorun"
require "rack/test"
require "securerandom"
require "tmpdir"
require_relative "app"

HEADER = "name,email,phone,country,signup_date\n".freeze

class ImporterTest < Minitest::Test
  def setup
    @dir = Dir.mktmpdir
    @db_path = File.join(@dir, "test.sqlite3")
  end

  def teardown
    FileUtils.remove_entry(@dir)
  end

  def run_import(csv_text)
    id = SecureRandom.uuid
    path = File.join(@dir, "#{id}.csv")
    File.write(path, csv_text)
    CsvImport.create_import(@db_path, id)
    CsvImport::Importer.new(@db_path, id, path).run
    id
  end

  def query(sql, *args)
    db = CsvImport.open_db(@db_path)
    db.execute(sql, args)
  ensure
    db&.close
  end

  def import_row(id)
    query("SELECT * FROM imports WHERE id = ?", id).first
  end

  def customers
    query("SELECT * FROM customers ORDER BY email")
  end

  def test_inserts_valid_rows_and_normalises_fields
    id = run_import(HEADER + <<~CSV)
      Ann Lee,  Ann@Example.COM ,+1 555 0100,us,2024-02-29
      Bob,bob@example.com,,DE,2023-07-01
    CSV

    import = import_row(id)
    assert_equal "completed", import["status"]
    assert_equal 2, import["processed_rows"]
    assert_equal 2, import["inserted"]
    assert_equal 0, import["updated"]
    assert_equal 0, import["failed"]

    rows = customers
    assert_equal ["ann@example.com", "bob@example.com"], rows.map { |r| r["email"] }
    assert_equal "US", rows.first["country"]
    assert_equal "+1 555 0100", rows.first["phone"]
    assert_nil rows.last["phone"]
  end

  def test_updates_existing_customer_matched_by_email
    run_import(HEADER + "Ann,ann@example.com,,US,2024-01-01\n")
    id = run_import(HEADER + "Ann Lee,ANN@example.com,+44 20 7946 0000,GB,2024-01-01\n")

    import = import_row(id)
    assert_equal 0, import["inserted"]
    assert_equal 1, import["updated"]

    rows = customers
    assert_equal 1, rows.size
    assert_equal "Ann Lee", rows.first["name"]
    assert_equal "GB", rows.first["country"]
  end

  def test_records_row_errors_and_keeps_going
    id = run_import(HEADER + <<~CSV)
      Good,good@example.com,,US,2024-05-05
      Bad Email,not-an-email,,US,2024-05-05
      No Country,nc@example.com,,,2024-05-05
      Bad Date,bd@example.com,,US,05/05/2024
    CSV

    import = import_row(id)
    assert_equal "completed", import["status"]
    assert_equal 4, import["processed_rows"]
    assert_equal 1, import["inserted"]
    assert_equal 3, import["failed"]

    errors = query("SELECT line, message FROM import_errors WHERE import_id = ? ORDER BY line", id)
    assert_equal [3, 4, 5], errors.map { |e| e["line"] }
    assert_includes errors[0]["message"], "email is invalid"
    assert_includes errors[1]["message"], "country"
    assert_includes errors[2]["message"], "signup_date"
    assert_equal ["good@example.com"], customers.map { |r| r["email"] }
  end

  def test_missing_columns_fail_the_import
    id = run_import("name,email\nAnn,ann@example.com\n")

    import = import_row(id)
    assert_equal "failed", import["status"]
    assert_includes import["fatal_error"], "missing columns: phone, country, signup_date"
    assert_empty customers
  end

  def test_validate_row_rejects_impossible_dates
    row = { "name" => "A", "email" => "a@b.co", "phone" => "", "country" => "us", "signup_date" => "2024-13-01" }
    customer, errors = CsvImport.validate_row(row)

    assert_nil customer
    assert_equal ["signup_date must be YYYY-MM-DD"], errors
  end
end

class ApiTest < Minitest::Test
  include Rack::Test::Methods

  CSV_TEXT = HEADER + <<~CSV
    Ann,ann@example.com,,US,2024-01-01
    Bad,not-an-email,,US,2024-01-01
  CSV

  def app
    CsvImport::App
  end

  def setup
    @dir = Dir.mktmpdir
    CsvImport::App.set :database_path, File.join(@dir, "api.sqlite3")
    CsvImport::App.set :uploads_dir, File.join(@dir, "uploads")
  end

  def teardown
    FileUtils.remove_entry(@dir)
  end

  def upload(text)
    path = File.join(@dir, "upload.csv")
    File.write(path, text)
    Rack::Test::UploadedFile.new(path, "text/csv")
  end

  def wait_for_import(id)
    CsvImport::App::JOBS.fetch(id).join
  end

  def test_upload_returns_id_and_progress_reports_errors
    post "/imports", file: upload(CSV_TEXT)
    assert_equal 202, last_response.status
    id = JSON.parse(last_response.body)["id"]
    wait_for_import(id)

    get "/imports/#{id}"
    assert_equal 200, last_response.status
    body = JSON.parse(last_response.body)
    assert_equal "completed", body["status"]
    assert_equal 2, body["processed_rows"]
    assert_equal 1, body["inserted"]
    assert_equal 1, body["failed"]
    assert_equal 1, body["errors_total"]
    assert_equal [{ "line" => 3, "message" => "email is invalid" }], body["errors"]
  end

  def test_error_list_is_paged
    post "/imports", file: upload(HEADER + "x,bad1,,US,2024-01-01\nx,bad2,,US,2024-01-01\n")
    id = JSON.parse(last_response.body)["id"]
    wait_for_import(id)

    get "/imports/#{id}?limit=1&offset=1"
    body = JSON.parse(last_response.body)
    assert_equal 2, body["errors_total"]
    assert_equal [3], body["errors"].map { |e| e["line"] }
  end

  def test_unknown_import_is_404
    get "/imports/does-not-exist"
    assert_equal 404, last_response.status
  end

  def test_upload_without_file_is_400
    post "/imports", {}
    assert_equal 400, last_response.status
  end
end
