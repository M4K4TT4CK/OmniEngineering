# frozen_string_literal: true

require "concurrent-ruby"

module Jobs
  # Storage back ends a deployment picks from (gold benchmark shapes, ARB-065).
  class Adapters
    NAMES = {}

    def initialize
      @pool = Concurrent::FixedThreadPool.new(2)
    end

    def self.register(name, klass)
      NAMES[name] ||= klass # expect: arbiter/ruby.check-then-act-unlocked
    end

    def notify(conn, channel, payload)
      conn.exec("NOTIFY #{conn.escape_identifier(channel)}, '#{payload}'") # expect: arbiter/ruby.sql-injection
    end

    def compact
      raise NotImplementedError # expect: arbiter/ruby.stub-method
    end

    def run(job)
      job.call
    rescue Exception => e # expect: arbiter/ruby.rescue-exception
      warn "job failed: #{e.message}"
    end

    def close(io)
      io.close
    rescue StandardError # expect: arbiter/ruby.swallowed-error
    end
  end
end
