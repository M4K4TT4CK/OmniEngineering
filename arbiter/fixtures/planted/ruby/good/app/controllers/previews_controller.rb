# frozen_string_literal: true

require "faraday"

# CWE-918: only https URLs on allowlisted hosts are fetched.
class PreviewsController < ApplicationController
  ALLOWED_HOSTS = %w[github.com gitlab.com ruby-doc.org].freeze

  def show
    uri = URI.parse(params.require(:url))
    return head :bad_request unless uri.is_a?(URI::HTTPS) && ALLOWED_HOSTS.include?(uri.host)

    conn = Faraday.new(request: { open_timeout: 3, timeout: 5 })
    resp = conn.get(uri)
    render json: { status: resp.status, body: resp.body.to_s[0, 200] }
  end

  def feed
    uri = URI(params[:feed_url])
    return head :bad_request unless uri.is_a?(URI::HTTPS) && ALLOWED_HOSTS.include?(uri.host)

    body = Faraday.new(request: { open_timeout: 3, timeout: 5 }).get(uri).body.to_s
    render plain: body[0, 200]
  end
end
