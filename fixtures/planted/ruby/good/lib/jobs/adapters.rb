# frozen_string_literal: true

require "concurrent-ruby"

module Jobs
  # Storage back ends a deployment picks from (gold benchmark shapes, ARB-065).
  class Adapters
    NAMES = Concurrent::Map.new

    def initialize(errors)
      @pool = Concurrent::FixedThreadPool.new(2)
      @errors = errors
    end

    def self.register(name, klass)
      NAMES[name] ||= klass
    end

    def notify(conn, channel, payload)
      conn.exec("NOTIFY #{conn.escape_identifier(channel)}, '#{conn.escape_string(payload)}'")
    end

    # Each back end implements these two.
    def compact
      raise NotImplementedError
    end

    def vacuum
      raise NotImplementedError
    end

    def rename_table(_from, _to)
      raise NotImplementedError, "rename_table is not supported by this adapter"
    end

    def run(job)
      job.call
    rescue Exception => e
      @errors.unknown_error(e, "job")
    end

    def run_hook(hook)
      hook.call
    rescue Exception => e # rubocop:disable Lint/RescueException
      warn "hook failed: #{e.message}"
    end

    def close(io)
      io.close
    rescue StandardError
      # the peer closed it first; there is nothing left to release
    end
  end
end
