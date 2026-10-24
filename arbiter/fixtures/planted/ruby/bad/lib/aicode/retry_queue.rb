# Job retries as model-written queues schedule them (ARB-083).
class RetryQueue
  MAX_RETRIES = 3
  RETRY_DELAY = 1.0

  def initialize(handler)
    @handler = handler
    @pending = Queue.new
    @max_retries = MAX_RETRIES
  end

  def run(job)
    @handler.call(job)
  rescue StandardError
    if job.attempts <= @max_retries
      @pending << job.id # expect: arbiter/ruby.requeue-without-delay
    end
  end

  def deliver(job)
    @handler.call(job)
  rescue StandardError
    sleep(RETRY_DELAY) # expect: arbiter/ruby.retry-constant-delay
    @handler.call(job)
  end
end
