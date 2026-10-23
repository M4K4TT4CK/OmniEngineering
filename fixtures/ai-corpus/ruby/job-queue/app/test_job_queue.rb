ENV["RACK_ENV"] = "test"

require "minitest/autorun"
require "rack/test"
require "tmpdir"
require_relative "app"

class FakeHandler
  attr_reader :calls

  def initialize(&block)
    @block = block
    @calls = Queue.new
  end

  def validate(payload); end

  def call(payload)
    @calls << payload
    @block ? @block.call(payload) : { "ok" => true }
  end
end

module Waiting
  def wait_for(queue, id, timeout: 5)
    deadline = Time.now + timeout
    loop do
      job = queue.status(id)
      return job if %w[completed failed].include?(job[:status])
      flunk "job #{id} did not finish: #{job.inspect}" if Time.now > deadline
      sleep 0.01
    end
  end
end

class JobQueueTest < Minitest::Test
  include Waiting

  def teardown
    @queue&.shutdown
  end

  def test_completes_job_and_stores_result
    @queue = JobQueue.new(handlers: { "echo" => FakeHandler.new { |p| { "got" => p["x"] } } }, workers: 2)
    id = @queue.enqueue("echo", { "x" => 1 })
    job = wait_for(@queue, id)
    assert_equal "completed", job[:status]
    assert_equal({ "got" => 1 }, job[:result])
    assert_equal 1, job[:attempts]
  end

  def test_retries_then_succeeds
    tries = 0
    handler = FakeHandler.new { tries += 1; raise "boom" if tries < 3; { "tries" => tries } }
    @queue = JobQueue.new(handlers: { "flaky" => handler }, workers: 1)
    job = wait_for(@queue, @queue.enqueue("flaky", {}))
    assert_equal "completed", job[:status]
    assert_equal 3, job[:attempts]
  end

  def test_fails_after_three_retries
    handler = FakeHandler.new { raise "always" }
    @queue = JobQueue.new(handlers: { "bad" => handler }, workers: 1)
    job = wait_for(@queue, @queue.enqueue("bad", {}))
    assert_equal "failed", job[:status]
    assert_equal 4, job[:attempts] # 1 attempt + 3 retries
    assert_match(/always/, job[:error])
  end

  def test_rejects_unknown_type_and_bad_payload
    @queue = JobQueue.new(handlers: { "echo" => FakeHandler.new }, workers: 1)
    assert_raises(JobQueue::UnknownJobType) { @queue.enqueue("nope", {}) }
    assert_raises(ArgumentError) { @queue.enqueue("echo", "not a hash") }
  end

  def test_runs_jobs_concurrently
    barrier = Queue.new
    handler = FakeHandler.new do
      barrier << 1
      Timeout.timeout(3) { sleep 0.01 until barrier.size >= 3 } # only passes if 3 run at once
      { "ok" => true }
    end
    @queue = JobQueue.new(handlers: { "slow" => handler }, workers: 3, max_retries: 0)
    ids = 3.times.map { @queue.enqueue("slow", {}) }
    ids.each { |id| assert_equal "completed", wait_for(@queue, id)[:status] }
  end

  def test_unknown_id_returns_nil
    @queue = JobQueue.new(handlers: {}, workers: 1)
    assert_nil @queue.status("missing")
  end
end

class HandlersTest < Minitest::Test
  include Waiting

  def setup
    Mail::TestMailer.deliveries.clear
    ENV["SMTP_FROM"] = "noreply@example.com"
  end

  def test_send_email_delivers
    handler = Handlers::SendEmail.new(delivery: [:test, {}], from: "noreply@example.com")
    payload = { "to" => "a@example.com", "subject" => "Hi", "body" => "Hello" }
    handler.validate(payload)
    assert_equal({ "delivered_to" => "a@example.com" }, handler.call(payload))
    mail = Mail::TestMailer.deliveries.last
    assert_equal ["a@example.com"], mail.to
    assert_equal "Hi", mail.subject
    assert_equal "Hello", mail.body.to_s
  end

  def test_send_email_validation
    handler = Handlers::SendEmail.new(delivery: [:test, {}], from: "x@example.com")
    ok = { "to" => "a@example.com", "subject" => "s", "body" => "b" }
    assert_raises(ArgumentError) { handler.validate(ok.merge("to" => "nope")) }
    assert_raises(ArgumentError) { handler.validate(ok.merge("to" => "a@example.com,b@example.com")) }
    assert_raises(ArgumentError) { handler.validate(ok.merge("subject" => "a\nBcc: evil@example.com")) }
    assert_raises(ArgumentError) { handler.validate(ok.reject { |k, _| k == "body" }) }
  end

  def test_send_email_requires_smtp_config
    handler = Handlers::SendEmail.new(from: "x@example.com")
    old = ENV.delete("SMTP_ADDRESS")
    assert_raises(RuntimeError) { handler.call({ "to" => "a@example.com", "subject" => "s", "body" => "b" }) }
  ensure
    ENV["SMTP_ADDRESS"] = old if old
  end

  def test_resize_image_validation
    handler = Handlers::ResizeImage.new(root: nil)
    assert_raises(ArgumentError) { handler.validate({}) }
    assert_raises(ArgumentError) { handler.validate({ "path" => "/tmp/a.png" }) }
    assert_raises(ArgumentError) { handler.validate({ "path" => "/tmp/a.png", "width" => -5 }) }
    handler.validate({ "path" => "/tmp/a.png", "width" => 100 })
  end

  def test_resize_image_enforces_root
    Dir.mktmpdir do |root|
      handler = Handlers::ResizeImage.new(root: root)
      handler.validate({ "path" => File.join(root, "a.png"), "width" => 10 })
      assert_raises(ArgumentError) { handler.validate({ "path" => "/etc/passwd", "width" => 10 }) }
      assert_raises(ArgumentError) do
        handler.validate({ "path" => File.join(root, "..", "x.png"), "width" => 10 })
      end
    end
  end

  def test_resize_image_missing_file_fails
    handler = Handlers::ResizeImage.new(root: nil)
    assert_raises(Errno::ENOENT) { handler.call({ "path" => "/nonexistent/a.png", "width" => 10 }) }
  end
end

class AppTest < Minitest::Test
  include Rack::Test::Methods
  include Waiting

  def app
    App
  end

  def setup
    @queue = JobQueue.new(handlers: { "echo" => FakeHandler.new { |p| p } }, workers: 2)
    App.set :queue, @queue
  end

  def teardown
    @queue.shutdown
  end

  def post_job(body)
    post "/jobs", body.is_a?(String) ? body : body.to_json, "CONTENT_TYPE" => "application/json"
  end

  def test_submit_and_poll
    post_job({ type: "echo", payload: { a: 1 } })
    assert_equal 202, last_response.status
    id = JSON.parse(last_response.body)["id"]

    wait_for(@queue, id)
    get "/jobs/#{id}"
    assert_equal 200, last_response.status
    body = JSON.parse(last_response.body)
    assert_equal "completed", body["status"]
    assert_equal({ "a" => 1 }, body["result"])
  end

  def test_unknown_type_is_422
    post_job({ type: "nope", payload: {} })
    assert_equal 422, last_response.status
  end

  def test_invalid_json_is_400
    post_job("{not json")
    assert_equal 400, last_response.status
  end

  def test_missing_job_is_404
    get "/jobs/does-not-exist"
    assert_equal 404, last_response.status
  end
end
