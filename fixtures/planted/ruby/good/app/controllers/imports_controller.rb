# frozen_string_literal: true

# CWE-77: only https URLs, and `--` ends git's options.
class ImportsController < ApplicationController
  def create
    uri = URI.parse(params.require(:repo_url))
    return head :bad_request unless uri.is_a?(URI::HTTPS)

    dir = Dir.mktmpdir("import-")
    ok = system("git", "clone", "--", uri.to_s, dir)
    render json: { dir: dir }, status: ok ? :created : :unprocessable_entity
  end
end
