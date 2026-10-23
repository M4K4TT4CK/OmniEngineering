# frozen_string_literal: true

require "faraday"

# CWE-918: the server fetches whatever URL the caller names, metadata endpoints included.
class PreviewsController < ApplicationController
  def show
    conn = Faraday.new(request: { open_timeout: 3, timeout: 5 })
    resp = conn.get(params.require(:url)) # expect: arbiter/ruby.ssrf-request-url
    render json: { status: resp.status, body: resp.body.to_s[0, 200] }
  end

  def feed
    uri = URI(params[:feed_url])
    body = Net::HTTP.get(uri) # expect: arbiter/ruby.ssrf-request-url, arbiter/ruby.http-no-timeout
    render plain: body[0, 200]
  end
end
