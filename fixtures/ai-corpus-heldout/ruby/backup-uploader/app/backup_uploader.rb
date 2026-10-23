#!/usr/bin/env ruby
# frozen_string_literal: true

# Scheduled backup uploader. Tars the configured directories, uploads the
# archive to an S3-compatible bucket, deletes expired archives, logs each run,
# and serves the last run's result over HTTP.

require "aws-sdk-s3"
require "json"
require "logger"
require "optparse"
require "rubygems/package"
require "rufus-scheduler"
require "socket"
require "tmpdir"
require "yaml"
require "zlib"

module BackupUploader
  ARCHIVE_SUFFIX = ".tar.gz"

  class Config
    REQUIRED_KEYS = %w[schedule directories bucket].freeze

    def self.load(path)
      new(YAML.safe_load_file(path))
    end

    def initialize(data)
      missing = REQUIRED_KEYS - data.keys
      raise ArgumentError, "config is missing: #{missing.join(', ')}" unless missing.empty?

      @data = data
    end

    def schedule = @data.fetch("schedule")
    def directories = Array(@data.fetch("directories"))
    def bucket = @data.fetch("bucket")
    def prefix = @data.fetch("prefix", "backups/")
    def endpoint = @data["endpoint"]
    def region = @data.fetch("region", "us-east-1")
    def retention_days = Integer(@data.fetch("retention_days", 30))
    def status_port = Integer(@data.fetch("status_port", 8080))
    def log_file = @data["log_file"]
  end

  # Writes a gzip-compressed tar of the given directories. Pure Ruby, so no
  # shell is involved and paths are never interpolated into a command.
  module Archiver
    def self.create(directories, output_path)
      Zlib::GzipWriter.open(output_path) do |gzip|
        Gem::Package::TarWriter.new(gzip) do |tar|
          directories.each { |dir| add_directory(tar, File.expand_path(dir)) }
        end
      end
      output_path
    end

    def self.add_directory(tar, root)
      base = File.basename(root)
      Dir.glob("**/*", File::FNM_DOTMATCH, base: root).sort.each do |rel|
        next if %w[. ..].include?(File.basename(rel))

        full = File.join(root, rel)
        next if File.symlink?(full)

        name = File.join(base, rel)
        if File.directory?(full)
          tar.mkdir(name, File.stat(full).mode)
        else
          add_file(tar, full, name)
        end
      end
    end

    def self.add_file(tar, path, name)
      File.open(path, "rb") do |file|
        tar.add_file_simple(name, File.stat(path).mode, file.size) { |io| IO.copy_stream(file, io) }
      end
    end
  end

  # Runs one backup at a time and remembers the result of the most recent run.
  class Runner
    attr_reader :last_run

    def self.build_client(config)
      options = { region: config.region, force_path_style: true }
      options[:endpoint] = config.endpoint if config.endpoint
      Aws::S3::Client.new(**options)
    end

    def initialize(config, s3: nil, logger: Logger.new($stdout))
      @config = config
      @s3 = s3 || self.class.build_client(config)
      @logger = logger
      @mutex = Mutex.new
      @last_run = nil
    end

    # Runs a backup, waiting for any run already in progress, and returns the result.
    def run
      @mutex.synchronize { execute }
    end

    private

    def execute
      started = Time.now.utc
      result = { "started_at" => started.iso8601, "status" => "ok" }
      begin
        result.merge!(backup(started))
      rescue StandardError => e
        result.merge!("status" => "error", "error" => "#{e.class}: #{e.message}")
      end
      result["finished_at"] = Time.now.utc.iso8601
      @last_run = result
      log(result)
      result
    end

    def backup(started)
      key = "#{@config.prefix}backup-#{started.strftime('%Y%m%dT%H%M%SZ')}#{ARCHIVE_SUFFIX}"
      Dir.mktmpdir("backup-uploader") do |dir|
        archive = Archiver.create(@config.directories, File.join(dir, File.basename(key)))
        File.open(archive, "rb") { |io| @s3.put_object(bucket: @config.bucket, key: key, body: io) }
        { "key" => key, "size_bytes" => File.size(archive), "deleted" => prune }
      end
    end

    # Deletes archives under the prefix whose age exceeds the retention period.
    def prune
      cutoff = Time.now - (@config.retention_days * 86_400)
      expired = []
      @s3.list_objects_v2(bucket: @config.bucket, prefix: @config.prefix).each_page do |page|
        page.contents.each do |object|
          next unless object.key.end_with?(ARCHIVE_SUFFIX) && object.last_modified < cutoff

          expired << object.key
        end
      end
      expired.each_slice(1000) do |batch|
        response = @s3.delete_objects(bucket: @config.bucket, delete: { objects: batch.map { |key| { key: key } } })
        raise "failed to delete #{response.errors.size} archive(s)" if response.errors.to_a.any?
      end
      expired.size
    end

    def log(result)
      if result["status"] == "ok"
        @logger.info("backup ok key=#{result['key']} size_bytes=#{result['size_bytes']} deleted=#{result['deleted']}")
      else
        @logger.error("backup failed: #{result['error']}")
      end
    end
  end

  # Minimal HTTP server: GET /status returns the last run as JSON.
  class StatusServer
    REASONS = { 200 => "OK", 404 => "Not Found" }.freeze

    attr_reader :port

    def initialize(runner, port:, host: "127.0.0.1")
      @runner = runner
      @host = host
      @port = port
    end

    def start
      @server = TCPServer.new(@host, @port)
      @port = @server.addr[1]
      @thread = Thread.new do
        loop { Thread.new(@server.accept) { |client| handle(client) } }
      end
      self
    end

    def stop
      @thread&.kill
      @server&.close
    end

    private

    def handle(client)
      method, path = client.gets.to_s.split
      if method == "GET" && path == "/status"
        respond(client, 200, @runner.last_run || { "status" => "never_run" })
      else
        respond(client, 404, { "error" => "not found" })
      end
    ensure
      client.close
    end

    def respond(client, code, body)
      payload = JSON.generate(body)
      client.write("HTTP/1.1 #{code} #{REASONS.fetch(code)}\r\n" \
                   "Content-Type: application/json\r\n" \
                   "Content-Length: #{payload.bytesize}\r\n" \
                   "Connection: close\r\n\r\n#{payload}")
    end
  end

  def self.main(argv)
    options = { config: "config.yml", once: false }
    OptionParser.new do |parser|
      parser.banner = "Usage: backup_uploader.rb [options]"
      parser.on("-c", "--config PATH", "Config file (default: config.yml)") { |path| options[:config] = path }
      parser.on("--once", "Run one backup now and exit") { options[:once] = true }
    end.parse!(argv)

    config = Config.load(options[:config])
    logger = Logger.new(config.log_file || $stdout)
    runner = Runner.new(config, logger: logger)

    return runner.run["status"] == "ok" ? 0 : 1 if options[:once]

    StatusServer.new(runner, port: config.status_port).start
    scheduler = Rufus::Scheduler.new
    scheduler.cron(config.schedule) { runner.run }
    logger.info("scheduled #{config.schedule.inspect}; status at http://127.0.0.1:#{config.status_port}/status")
    scheduler.join
    0
  end
end

exit BackupUploader.main(ARGV) if $PROGRAM_NAME == __FILE__
