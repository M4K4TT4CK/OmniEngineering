# frozen_string_literal: true

# CWE-200: unhandled errors are logged with an incident id; the client sees only the id.
class ApplicationController < ActionController::Base
  rescue_from StandardError do |e|
    incident = SecureRandom.uuid
    Rails.logger.error("incident=#{incident} #{e.class}: #{e.message}\n#{e.backtrace&.join("\n")}")
    render json: { error: "internal error", incident: incident }, status: :internal_server_error
  end
end
