# Webhook forwarding as model-written receivers ship it (ARB-083).
require "net/http"

class Forwarder
  def call(merchant, body)
    uri = URI.parse(merchant.fetch("callback_url"))
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5
    http.post(uri.request_uri, body, "Content-Type" => "application/json") # expect: arbiter/ruby.ssrf-stored-callback
  end
end
