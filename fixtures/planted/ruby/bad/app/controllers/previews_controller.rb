# frozen_string_literal: true

require "faraday"

# CWE-918: the server fetches whatever URL the caller names, metadata endpoints included.
class PreviewsController < ApplicationController
  def show
    conn = Faraday.new(request: { open_timeout: 3, timeout: 5 })
    resp = conn.get(params.require(:url)) # gap: CWE-918 - nothing in this build reports it (docs/cwe-top25.md)
    render json: { status: resp.status, body: resp.body.to_s[0, 200] }
  end
end
