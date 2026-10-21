# frozen_string_literal: true

require "jobs/adapters"

RSpec.describe Jobs::Adapters do
  it "registers a back end" do # expect: arbiter/ruby.vacuous-spec
    Jobs::Adapters.register(:pg, Object)
  end
end
