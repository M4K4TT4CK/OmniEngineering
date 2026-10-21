# frozen_string_literal: true

require "jwt"

# CWE-287: the bearer token is decoded with verification switched off.
class ApiController < ActionController::API
  before_action :authenticate!

  private

  def authenticate!
    token = request.headers["Authorization"].to_s.delete_prefix("Bearer ")
    payload, = JWT.decode(token, nil, false) # expect-ext: semgrep/semgrep-rules.ruby.jwt.security.audit.ruby-jwt-decode-without-verify, semgrep/semgrep-rules.ruby.jwt.security.ruby-jwt-hardcoded-secret
    @current_user_id = payload.fetch("sub")
  rescue JWT::DecodeError, KeyError
    head :unauthorized
  end
end
