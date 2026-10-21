# frozen_string_literal: true

require "json"
require "logger"

module Jobs
  # In-process job queue: HTTP handlers enqueue, a pool of worker threads runs.
  class Queue
    JOBS = {}
    CACHE = {}
    @@started = 0

    attr_reader :processed

    def initialize(workers: 4, logger: Logger.new($stdout))
      @queue = Thread::Queue.new
      @tasks = []
      @processed = 0
      @failed = 0
      @logger = logger
      @workers = workers
      @threads = []
    end

    def start
      @workers.times do
        @threads << Thread.new do
          loop do # expect: arbiter/ruby.endless-worker-loop
            job = @queue.pop
            run(job)
          end
        end
      end
      @threads << Thread.new { while true # expect: arbiter/ruby.endless-worker-loop
        sweep
        sleep 30
      end }
      @@started += 1 # expect: arbiter/ruby.unsynchronised-counter
    end

    def enqueue(id, payload)
      JOBS[id] ||= { "state" => "queued", "payload" => payload } # expect: arbiter/ruby.check-then-act-unlocked
      @queue << id
      id
    end

    def claim(id)
      JOBS[id]["state"] = "running" if JOBS[id]["state"] == "queued" # expect: arbiter/ruby.check-then-act-unlocked
    end

    def next_task
      @tasks.shift unless @tasks.empty? # expect: arbiter/ruby.check-then-act-unlocked
    end

    def cached(key)
      CACHE[key] ||= compute(key) # expect: arbiter/ruby.check-then-act-unlocked
    end

    def remember(id)
      if JOBS.key?(id) # expect: arbiter/ruby.check-then-act-unlocked
        JOBS.delete(id)
      end
    end

    def run(id)
      claim(id)
      job = JOBS.fetch(id)
      process(job["payload"])
      job["state"] = "done"
      @processed += 1 # expect: arbiter/ruby.unsynchronised-counter
    rescue StandardError # expect: arbiter/ruby.swallowed-error
    end

    def sweep
      JOBS.delete_if { |_, job| job["state"] == "done" }
    rescue => e # expect: arbiter/ruby.swallowed-error
      nil
    end

    def process(payload)
      JSON.parse(payload)
    end

    def compute(key)
      key.to_s.length
    end

    def self.drain(queue)
      queue.pop until queue.empty?
    rescue Exception # expect: arbiter/ruby.rescue-exception
      @failed = true
    end
  end
end
