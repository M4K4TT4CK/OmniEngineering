# frozen_string_literal: true

require "jwt"

# CWE-287: the bearer token's signature and algorithm are verified before it is trusted.
class ApiController < ActionController::API
  before_action :authenticate!

  private

  def authenticate!
    token = request.headers["Authorization"].to_s.delete_prefix("Bearer ")
    payload, = JWT.decode(token, ENV.fetch("JWT_SECRET"), true, algorithm: "HS256")
    @current_user_id = payload.fetch("sub")
  rescue JWT::DecodeError, KeyError
    head :unauthorized
  end
end
