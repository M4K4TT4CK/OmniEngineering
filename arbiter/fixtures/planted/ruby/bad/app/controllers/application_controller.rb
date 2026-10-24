# frozen_string_literal: true

# CWE-200: every unhandled error goes back to the client with its message and backtrace.
class ApplicationController < ActionController::Base
  rescue_from StandardError do |e|
    render json: { error: e.message, backtrace: e.backtrace }, status: :internal_server_error # expect: arbiter/ruby.error-backtrace-to-client
  end
end
