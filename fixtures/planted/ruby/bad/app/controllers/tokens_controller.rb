# frozen_string_literal: true

require "jwt"

# CWE-798: the signing secret and the database password are literals in the source.
class TokensController < ApplicationController
  SIGNING_KEY = "s3cr3t-signing-key-do-not-share" # expect-ext: semgrep/semgrep-rules.ruby.jwt.security.ruby-jwt-hardcoded-secret

  def create
    ActiveRecord::Base.establish_connection(adapter: "postgresql", host: "db.internal", username: "app_user", password: "Tr1cky-Pr0d-Pw") # expect: arbiter/secrets.assigned-credential expect-ext: semgrep/semgrep-rules.generic.secrets.gitleaks.generic-api-key
    render json: { token: JWT.encode({ sub: current_user.id }, SIGNING_KEY, "HS256") }
  end
end
