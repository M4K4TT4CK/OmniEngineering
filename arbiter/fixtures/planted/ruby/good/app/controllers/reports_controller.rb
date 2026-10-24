# frozen_string_literal: true

# CWE-22: the resolved path must stay under the reports directory.
class ReportsController < ApplicationController
  REPORTS = Rails.root.join("storage", "reports").realpath

  def show
    path = REPORTS.join(File.basename(params[:name].to_s))
    raise ActionController::RoutingError, "Not Found" unless path.file? && path.realpath.to_s.start_with?("#{REPORTS}/")

    send_file path, type: "application/pdf"
  end
end
