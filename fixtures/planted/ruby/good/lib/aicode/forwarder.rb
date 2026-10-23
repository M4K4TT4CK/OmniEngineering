# Webhook forwarding that refuses internal addresses (ARB-083).
require "ipaddr"
require "net/http"
require "resolv"

class Forwarder
  def call(merchant, body)
    uri = URI.parse(merchant.fetch("callback_url"))
    addrs = Resolv.getaddresses(uri.host).map { |a| IPAddr.new(a) }
    return nil if addrs.empty? || addrs.any? { |ip| ip.private? || ip.loopback? || ip.link_local? }

    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5
    http.post(uri.request_uri, body, "Content-Type" => "application/json")
  end
end
