# frozen_string_literal: true

require "net/http"
require "uri"
require "json"
require "openssl"
require "faraday"
require "httparty"

module Jobs
  # Calls the upstream job-status service.
  class Client
    OPEN_TIMEOUT = 2
    READ_TIMEOUT = 5
    MAX_ATTEMPTS = 3

    def initialize(base_url)
      @base = URI(base_url)
    end

    def status(id)
      uri = URI("#{@base}/jobs/#{id}")
      res = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == "https",
                            open_timeout: OPEN_TIMEOUT, read_timeout: READ_TIMEOUT) do |http|
        http.get(uri.request_uri)
      end
      JSON.parse(res.body)
    end

    def submit(payload)
      http = Net::HTTP.new(@base.host, @base.port)
      http.use_ssl = true
      http.verify_mode = OpenSSL::SSL::VERIFY_PEER
      http.open_timeout = OPEN_TIMEOUT
      http.read_timeout = READ_TIMEOUT
      http.post("/jobs", JSON.dump(payload), "Content-Type" => "application/json")
    end

    def health
      HTTParty.get("#{@base}/health", timeout: READ_TIMEOUT)
    end

    def connection
      Faraday.new(url: @base.to_s, request: { open_timeout: OPEN_TIMEOUT, timeout: READ_TIMEOUT }) do |f|
        f.request :json
        f.response :json
      end
    end

    def fetch_with_retry(id)
      attempts = 0
      begin
        status(id)
      rescue Faraday::Error, SocketError
        attempts += 1
        raise if attempts >= MAX_ATTEMPTS
        sleep((2**attempts) * 0.1 * (0.5 + rand))
        retry
      end
    end

    def cancel(id)
      connection.delete("/jobs/#{id}")
      true
    rescue Faraday::Error => e
      warn("cancel #{id}: #{e.message}")
      false
    end

    # One-shot retry: a stale keep-alive socket is reopened once, then the error stands.
    def status_once_more(id)
      reconnected = false
      begin
        status(id)
      rescue EOFError
        raise if reconnected

        @conn = nil
        reconnected = true
        retry
      end
    end

    def snapshot(options)
      Marshal.load(Marshal.dump(options))
    end

    def on_error(handlers, err)
      handlers.each do |handler|
        handler.call(err)
      rescue Exception => e # rubocop:disable Lint/RescueException
        warn("error handler failed: #{e.message}")
      end
    end

    def close
      @conn&.close
    rescue IOError
      # already closed: a named failure, deliberately ignored
    end
  end
end
