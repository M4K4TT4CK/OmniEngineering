# frozen_string_literal: true

require "fileutils"
require "json"
require "logger"
require "net/http"
require "stringio"
require "tmpdir"
require "minitest/autorun"
require_relative "backup_uploader"

class ConfigTest < Minitest::Test
  def test_requires_schedule_directories_and_bucket
    error = assert_raises(ArgumentError) { BackupUploader::Config.new("bucket" => "b") }
    assert_match "schedule", error.message
    assert_match "directories", error.message
  end
end

class ArchiverTest < Minitest::Test
  def test_archives_nested_files_as_gzip_tar
    Dir.mktmpdir do |dir|
      source = File.join(dir, "docs")
      FileUtils.mkdir_p(File.join(source, "sub"))
      File.write(File.join(source, "one.txt"), "first")
      File.write(File.join(source, "sub", "two.txt"), "second")
      archive = File.join(dir, "out.tar.gz")

      BackupUploader::Archiver.create([source], archive)

      entries = {}
      Zlib::GzipReader.open(archive) do |gzip|
        Gem::Package::TarReader.new(gzip) do |tar|
          tar.each { |entry| entries[entry.full_name] = entry.file? ? entry.read : nil }
        end
      end
      assert_equal "first", entries["docs/one.txt"]
      assert_equal "second", entries["docs/sub/two.txt"]
    end
  end
end

# Shared fixtures: a stubbed S3 client (no network) and a config pointing at a temp dir.
class BackupTest < Minitest::Test
  def setup
    @dir = Dir.mktmpdir
    File.write(File.join(@dir, "data.txt"), "hello")
    @s3 = Aws::S3::Client.new(
      stub_responses: true,
      region: "us-east-1",
      credentials: Aws::Credentials.new("test", "test")
    )
  end

  def teardown
    FileUtils.remove_entry(@dir)
  end

  def config(overrides = {})
    BackupUploader::Config.new(
      { "schedule" => "0 2 * * *", "directories" => [@dir], "bucket" => "test-bucket", "retention_days" => 30 }
        .merge(overrides)
    )
  end

  def runner
    BackupUploader::Runner.new(config, s3: @s3, logger: Logger.new(StringIO.new))
  end

  def api_request(operation)
    @s3.api_requests.find { |request| request[:operation_name] == operation }
  end
end

class RunnerTest < BackupTest
  def test_run_uploads_archive_and_records_success
    @s3.stub_responses(:list_objects_v2, contents: [])
    backup = runner
    result = backup.run

    assert_equal "ok", result["status"]
    assert_equal result, backup.last_run
    put = api_request(:put_object)
    assert_equal "test-bucket", put[:params][:bucket]
    assert_equal result["key"], put[:params][:key]
    assert_match %r{\Abackups/backup-\d{8}T\d{6}Z\.tar\.gz\z}, put[:params][:key]
  end

  def test_prune_deletes_only_expired_archives
    old = Time.now - (100 * 86_400)
    recent = Time.now - (2 * 86_400)
    @s3.stub_responses(:list_objects_v2, contents: [
      { key: "backups/backup-old.tar.gz", last_modified: old, size: 1 },
      { key: "backups/backup-recent.tar.gz", last_modified: recent, size: 1 },
      { key: "backups/notes.txt", last_modified: old, size: 1 }
    ])

    result = runner.run

    assert_equal "ok", result["status"]
    assert_equal 1, result["deleted"]
    delete = api_request(:delete_objects)
    assert_equal [{ key: "backups/backup-old.tar.gz" }], delete[:params][:delete][:objects]
  end

  def test_failed_upload_is_recorded_and_skips_pruning
    @s3.stub_responses(:put_object, "NoSuchBucket")
    backup = runner

    result = backup.run

    assert_equal "error", result["status"]
    assert_match "NoSuchBucket", result["error"]
    assert_equal result, backup.last_run
    assert_nil api_request(:delete_objects)
  end
end

class StatusServerTest < BackupTest
  def test_reports_never_run_then_last_run
    @s3.stub_responses(:list_objects_v2, contents: [])
    backup = runner
    server = BackupUploader::StatusServer.new(backup, port: 0).start

    response = Net::HTTP.get_response(URI("http://127.0.0.1:#{server.port}/status"))
    assert_equal "never_run", JSON.parse(response.body)["status"]

    backup.run
    response = Net::HTTP.get_response(URI("http://127.0.0.1:#{server.port}/status"))
    assert_equal "200", response.code
    assert_equal "ok", JSON.parse(response.body)["status"]
  ensure
    server&.stop
  end
end
