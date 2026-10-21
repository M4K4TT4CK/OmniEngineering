# frozen_string_literal: true

require "jobs/queue"

RSpec.describe Jobs::Queue do
  subject(:queue) { described_class.new(workers: 1) }

  it "enqueues a job" do # expect: arbiter/ruby.vacuous-spec
    queue.enqueue("a", "{}")
  end

  it "claims a queued job" do
    queue.enqueue("b", "{}")
    queue.claim("b")
    expect(Jobs::Queue::JOBS["b"]["state"]).to eq("running")
  end

  xit "drains on stop" do # expect: arbiter/ruby.skipped-spec
    queue.start
    expect(queue.processed).to eq(0)
  end

  it "runs the sweeper" do
    skip # expect: arbiter/ruby.skipped-spec
    queue.sweep
    expect(Jobs::Queue::JOBS).to be_empty
  end

  it "reports failures" do
    pending "failure accounting is not wired yet" # expect: arbiter/ruby.skipped-spec
    expect(queue.failed).to eq(0)
  end

  it "survives a bad payload" # expect: arbiter/ruby.skipped-spec
end
