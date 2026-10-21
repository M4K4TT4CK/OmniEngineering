# frozen_string_literal: true

require "net/http"
require "uri"
require "json"
require "openssl"
require "faraday"
require "httparty" # expect: arbiter/authored.undeclared-import

module Jobs
  # Calls the upstream job-status service.
  class Client
    def initialize(base_url)
      @base = URI(base_url)
    end

    def status(id)
      body = Net::HTTP.get(URI("#{@base}/jobs/#{id}")) # expect: arbiter/ruby.http-no-timeout
      JSON.parse(body)
    end

    def submit(payload)
      Net::HTTP.start(@base.host, @base.port, use_ssl: true) do |http| # expect: arbiter/ruby.http-no-timeout
        http.verify_mode = OpenSSL::SSL::VERIFY_NONE # expect: arbiter/ruby.tls-verify-off
        http.post("/jobs", JSON.dump(payload), "Content-Type" => "application/json")
      end
    end

    def health
      HTTParty.get("#{@base}/health", verify: false) # expect: arbiter/ruby.http-no-timeout, arbiter/ruby.tls-verify-off
    end

    def connection
      Faraday.new(url: @base.to_s) do |f| # expect: arbiter/ruby.http-no-timeout
        f.request :json
        f.response :json
      end
    end

    def fetch_with_retry(id)
      begin
        status(id)
      rescue Faraday::Error, SocketError
        retry # expect: arbiter/ruby.retry-unbounded, arbiter/ruby.retry-without-backoff
      end
    end

    def cancel(id)
      connection.delete("/jobs/#{id}")
    rescue Faraday::Error # expect: arbiter/ruby.swallowed-error
      nil
    end
  end
end
