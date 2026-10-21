# frozen_string_literal: true

require "jobs/queue"

RSpec.describe Jobs::Queue do
  subject(:queue) { described_class.new(workers: 1) }

  it "enqueues a job" do
    expect(queue.enqueue("a", "{}")).to eq("a")
  end

  it "claims a queued job" do
    queue.enqueue("b", "{}")
    queue.claim("b")
    expect(Jobs::Queue::JOBS["b"]["state"]).to eq("running")
  end

  it "drains on stop" do
    queue.start
    queue.stop
    expect(queue.processed).to eq(0)
  end

  it "runs the sweeper" do
    skip "sweeper needs a writable log dir" unless File.writable?("log")
    queue.sweep
    expect(Jobs::Queue::JOBS).to be_empty
  end

  it "does not raise on a bad payload" do
    expect { queue.run("missing") }.not_to raise_error
  end

  it { is_expected.to respond_to(:enqueue) }
end
