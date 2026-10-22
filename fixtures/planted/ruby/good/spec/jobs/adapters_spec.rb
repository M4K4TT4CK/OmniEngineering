# frozen_string_literal: true

require "jobs/adapters"

RSpec.describe Jobs::Adapters do
  it "registers a back end" do
    Jobs::Adapters.register(:pg, Object)
    should_be_registered :pg
  end

  it "does not raise when a back end registers twice" do
    Jobs::Adapters.register(:pg, Object)
    Jobs::Adapters.register(:pg, Object)
  end

  def should_be_registered(name)
    expect(Jobs::Adapters::NAMES[name]).not_to be_nil
  end
end
