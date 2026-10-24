require "securerandom"
require "mail"
require "mini_magick"

# In-memory background job queue with a pool of worker threads.
class JobQueue
  MAX_RETRIES = 3 # retries after the first attempt, so at most 4 attempts

  Job = Struct.new(:id, :type, :payload, :status, :attempts, :result, :error,
                   :created_at, :updated_at, keyword_init: true)

  class UnknownJobType < ArgumentError; end

  # handlers: { "type" => object responding to #validate(payload) and #call(payload) }
  def initialize(handlers:, workers: 4, max_retries: MAX_RETRIES)
    @handlers = handlers
    @max_retries = max_retries
    @jobs = {}
    @mutex = Mutex.new
    @pending = Queue.new
    @threads = Array.new(workers) { Thread.new { work_loop } }
  end

  # Returns the new job id. Raises UnknownJobType / ArgumentError on bad input.
  def enqueue(type, payload)
    handler = @handlers[type] or raise UnknownJobType, "unknown job type: #{type.inspect}"
    raise ArgumentError, "payload must be a JSON object" unless payload.is_a?(Hash)
    handler.validate(payload)

    now = Time.now
    job = Job.new(id: SecureRandom.uuid, type: type, payload: payload, status: "queued",
                  attempts: 0, created_at: now, updated_at: now)
    @mutex.synchronize { @jobs[job.id] = job }
    @pending << job.id
    job.id
  end

  # Returns a snapshot hash of the job, or nil.
  def status(id)
    @mutex.synchronize do
      job = @jobs[id]
      job && job.to_h.dup
    end
  end

  # Lets queued work finish, then stops the workers.
  def shutdown
    @threads.size.times { @pending << :stop }
    @threads.each(&:join)
  end

  private

  def work_loop
    while (id = @pending.pop) != :stop
      run(id)
    end
  end

  def run(id)
    job = @mutex.synchronize do
      j = @jobs[id]
      j.status = "running"
      j.attempts += 1
      j.updated_at = Time.now
      j
    end

    begin
      result = @handlers.fetch(job.type).call(job.payload)
      finish(job, "completed", result: result, error: nil)
    rescue StandardError => e
      message = "#{e.class}: #{e.message}"
      if job.attempts <= @max_retries
        finish(job, "queued", error: message)
        @pending << job.id
      else
        finish(job, "failed", error: message)
      end
    end
  end

  def finish(job, status, attrs)
    @mutex.synchronize do
      job.status = status
      job.updated_at = Time.now
      attrs.each { |k, v| job[k] = v }
    end
  end
end

module Handlers
  # Resizes an image with ImageMagick. Payload: path, width, height (at least one dimension).
  # If IMAGE_ROOT is set, only files under that directory may be processed.
  class ResizeImage
    def initialize(root: ENV["IMAGE_ROOT"])
      @root = root && File.realpath(root)
    end

    def validate(payload)
      path = payload["path"]
      raise ArgumentError, "path is required" unless path.is_a?(String) && !path.empty?
      raise ArgumentError, "path must not contain NUL" if path.include?("\0")
      dims = %w[width height].map { |k| payload[k] }
      raise ArgumentError, "width or height is required" if dims.all?(&:nil?)
      unless dims.all? { |d| d.nil? || (d.is_a?(Integer) && d.between?(1, 20_000)) }
        raise ArgumentError, "width/height must be integers between 1 and 20000"
      end
      resolve(path) # fail fast on paths outside the root
    end

    def call(payload)
      source = resolve(payload["path"])
      raise Errno::ENOENT, source unless File.file?(source)

      ext = File.extname(source)
      output = File.join(File.dirname(source), "#{File.basename(source, ext)}_resized#{ext}")
      image = MiniMagick::Image.open(source)
      image.resize("#{payload['width']}x#{payload['height']}")
      image.write(output)
      { "output_path" => output, "width" => image.width, "height" => image.height }
    end

    private

    def resolve(path)
      full = File.expand_path(path)
      if @root
        real = File.exist?(full) ? File.realpath(full) : full
        unless real.start_with?(@root + File::SEPARATOR)
          raise ArgumentError, "path is outside the allowed image root"
        end
      end
      full
    end
  end

  # Sends an email over SMTP. Payload: to, subject, body.
  # SMTP settings come from SMTP_ADDRESS, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
  # SMTP_FROM, SMTP_STARTTLS (default "true").
  class SendEmail
    def initialize(delivery: nil, from: ENV["SMTP_FROM"])
      @delivery = delivery
      @from = from
    end

    def validate(payload)
      %w[to subject body].each do |k|
        raise ArgumentError, "#{k} is required" unless payload[k].is_a?(String) && !payload[k].empty?
      end
      unless payload["to"].match?(/\A[^@\s,;<>]+@[^@\s,;<>]+\z/)
        raise ArgumentError, "to must be a single email address"
      end
      raise ArgumentError, "subject must be a single line" if payload["subject"].match?(/[\r\n]/)
    end

    def call(payload)
      from = @from or raise "SMTP_FROM is not configured"
      method, settings = @delivery || smtp_delivery
      mail = Mail.new do
        from from
        to payload["to"]
        subject payload["subject"]
        body payload["body"]
      end
      mail.delivery_method(method, settings)
      mail.deliver!
      { "delivered_to" => payload["to"] }
    end

    private

    def smtp_delivery
      address = ENV["SMTP_ADDRESS"] or raise "SMTP_ADDRESS is not configured"
      settings = {
        address: address,
        port: Integer(ENV.fetch("SMTP_PORT", "587")),
        enable_starttls_auto: ENV.fetch("SMTP_STARTTLS", "true") == "true"
      }
      if ENV["SMTP_USER"]
        settings.merge!(user_name: ENV["SMTP_USER"], password: ENV["SMTP_PASSWORD"],
                        authentication: :plain)
      end
      [:smtp, settings]
    end
  end

  def self.default
    { "resize-image" => ResizeImage.new, "send-email" => SendEmail.new }
  end
end
