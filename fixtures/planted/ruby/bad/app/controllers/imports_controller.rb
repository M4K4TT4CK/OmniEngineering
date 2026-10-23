# frozen_string_literal: true

# CWE-77 (argument injection, CWE-88): a repo of "--upload-pack=..." is an option to git.
class ImportsController < ApplicationController
  def create
    dir = Dir.mktmpdir("import-")
    ok = system("git", "clone", params.require(:repo_url), dir) # expect: arbiter/ruby.argument-injection
    render json: { dir: dir }, status: ok ? :created : :unprocessable_entity
  end
end
