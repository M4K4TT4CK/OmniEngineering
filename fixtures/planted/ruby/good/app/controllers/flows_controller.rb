# frozen_string_literal: true

# One-hop data flow (Milestone 3): the same shapes, done safely.
class FlowsController < ApplicationController
  GIT = "git"

  def search
    rows = Note.where(owner: params[:owner])
    render json: rows
  end

  def report
    name = File.basename(params.require(:name).to_s)
    path = File.join(REPORTS_DIR, name)
    send_data File.read(path)
  end

  def mirror
    repo = params[:repo]
    ok = system(GIT, "clone", "--", repo, "/srv/mirror")
    head(ok ? :created : :unprocessable_entity)
  end
end
