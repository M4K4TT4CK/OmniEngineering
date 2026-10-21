# frozen_string_literal: true

require "jwt"

# CWE-798: the signing secret and the database password come from the environment.
class TokensController < ApplicationController
  def create
    ActiveRecord::Base.establish_connection(ENV.fetch("DATABASE_URL"))
    render json: { token: JWT.encode({ sub: current_user.id }, ENV.fetch("JWT_SECRET"), "HS256") }
  end
end
