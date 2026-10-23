# frozen_string_literal: true

# One-hop data flow (Milestone 3): each defect is one step from its sink.
class FlowsController < ApplicationController
  GIT = "git"

  def search
    sql = owner_clause(params[:owner])
    rows = ActiveRecord::Base.connection.select_all(sql) # expect: arbiter/ruby.sql-injection
    render json: rows
  end

  def report
    name = params.require(:name)
    path = File.join(REPORTS_DIR, name) # expect: arbiter/ruby.path-traversal-request
    send_data File.read(path)
  end

  def mirror
    repo = params[:repo]
    ok = system(GIT, "clone", repo, "/srv/mirror") # expect: arbiter/ruby.argument-injection
    head(ok ? :created : :unprocessable_entity)
  end

  private

  def owner_clause(owner)
    "SELECT * FROM notes WHERE owner = '#{owner}'"
  end
end
