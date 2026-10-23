# Job retries with exponential backoff and jitter (ARB-083).
class RetryQueue
  MAX_RETRIES = 3
  RETRY_DELAY = 1.0

  def initialize(handler, scheduler)
    @handler = handler
    @scheduler = scheduler
    @max_retries = MAX_RETRIES
  end

  def run(job)
    @handler.call(job)
  rescue StandardError
    if job.attempts <= @max_retries
      wait = RETRY_DELAY * (2**job.attempts) * (0.5 + rand / 2)
      @scheduler.schedule(job.id, wait)
    end
  end
end
