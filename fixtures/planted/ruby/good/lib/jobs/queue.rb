# frozen_string_literal: true

require "json"
require "logger"
require "concurrent-ruby"

module Jobs
  # In-process job queue: HTTP handlers enqueue, a pool of worker threads runs.
  class Queue
    JOBS = {}
    CACHE = Concurrent::Map.new
    STOP = Object.new.freeze
    @@started = Concurrent::AtomicFixnum.new(0)

    def initialize(workers: 4, logger: Logger.new($stdout))
      @queue = Thread::Queue.new
      @tasks = []
      @processed = Concurrent::AtomicFixnum.new(0)
      @lock = Mutex.new
      @logger = logger
      @workers = workers
      @threads = []
      @stopping = false
    end

    def start
      @workers.times do
        @threads << Thread.new do
          loop do
            job = @queue.pop
            break if job.equal?(STOP)
            run(job)
          end
        end
      end
      @threads << Thread.new do
        sweep until @stopping
      end
      @@started.increment
    end

    def stop
      @stopping = true
      @workers.times { @queue << STOP }
      @threads.each(&:join)
    end

    def processed
      @processed.value
    end

    def enqueue(id, payload)
      @lock.synchronize do
        JOBS[id] ||= { "state" => "queued", "payload" => payload }
      end
      @queue << id
      id
    end

    def claim(id)
      @lock.synchronize do
        JOBS[id]["state"] = "running" if JOBS[id]["state"] == "queued"
      end
    end

    def next_task
      @lock.synchronize { @tasks.shift }
    end

    def cached(key)
      CACHE.compute_if_absent(key) { compute(key) }
    end

    def remember(id)
      @lock.synchronize { JOBS.delete(id) }
    end

    def run(id)
      claim(id)
      job = JOBS.fetch(id)
      process(job["payload"])
      job["state"] = "done"
      @processed.increment
    rescue JSON::ParserError => e
      @logger.warn("job #{id}: bad payload: #{e.message}")
      job["state"] = "failed" if job
    end

    def sweep
      @lock.synchronize { JOBS.delete_if { |_, job| job["state"] == "done" } }
      sleep 30
    rescue StandardError => e
      @logger.error("sweep failed: #{e.class}: #{e.message}")
      raise
    end

    def process(payload)
      JSON.parse(payload)
    end

    def compute(key)
      key.to_s.length
    end

    def self.drain(queue)
      queue.pop until queue.empty?
    rescue ThreadError => e
      Logger.new($stderr).warn("drain: #{e.message}")
      nil
    end
  end
end
