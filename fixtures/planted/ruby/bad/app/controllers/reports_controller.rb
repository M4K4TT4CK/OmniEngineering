# frozen_string_literal: true

# CWE-22: `?name=../../config/master.key` is sent like any other report.
class ReportsController < ApplicationController
  REPORTS = Rails.root.join("storage", "reports")

  def show
    send_file File.join(REPORTS, params[:name]), type: "application/pdf" # gap: CWE-22 - nothing in this build reports it (docs/cwe-top25.md)
  end
end
