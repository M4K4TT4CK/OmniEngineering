require "sinatra/base"
require "json"
require "digest"
require "fileutils"
require "securerandom"
require "net/http"
require "openssl"
require "resolv"
require "ipaddr"
require "vips"

begin
  # Refuse libvips loaders that are not hardened for untrusted input (libvips >= 8.13).
  Vips.block_untrusted(true) if Vips.respond_to?(:block_untrusted)
rescue StandardError
  nil
end

module Thumbnailer
  # An error that maps directly onto an HTTP response. Sinatra uses #http_status.
  class Error < StandardError
    attr_reader :http_status

    def initialize(message, http_status = 422)
      super(message)
      @http_status = http_status
    end
  end

  # Only these formats are accepted; detection is by magic bytes, never by
  # filename or client-supplied content type.
  FORMATS = {
    "jpg" => ->(d) { d.start_with?("\xFF\xD8\xFF".b) },
    "png" => ->(d) { d.start_with?("\x89PNG\r\n\x1A\n".b) },
    "gif" => ->(d) { d.start_with?("GIF87a".b, "GIF89a".b) },
    "webp" => ->(d) { d.byteslice(0, 4) == "RIFF".b && d.byteslice(8, 4) == "WEBP".b }
  }.freeze

  METADATA_PREFIXES = %w[exif- xmp- iptc-].freeze

  module_function

  def detect_format(data)
    FORMATS.find { |_, matches| matches.call(data) }&.first
  end

  # Reads only the header, so decompression bombs are rejected before decoding.
  def check_dimensions!(data, max_pixels)
    image = Vips::Image.new_from_buffer(data, "")
    return if image.width * image.height <= max_pixels

    raise Error.new("image dimensions exceed #{max_pixels} pixels", 413)
  rescue Vips::Error
    raise Error.new("could not decode image", 422)
  end

  # Resizes to the given width, preserving aspect ratio and never upscaling.
  # The output format is chosen by libvips from the extension of out_path.
  def resize(data, width, out_path)
    image = Vips::Image.thumbnail_buffer(data, width, height: 10_000_000, size: :down)
    # Drop EXIF/XMP/IPTC (GPS, camera, author...); orientation is already applied.
    fields = image.get_fields.select { |f| f.start_with?(*METADATA_PREFIXES) }
    image = image.mutate { |m| fields.each { |f| m.remove!(f) } }
    image.write_to_file(out_path)
  rescue Vips::Error
    raise Error.new("could not process image", 422)
  end

  # Content-addressed disk cache: <root>/<sha256 of source>/<width>.<ext>.
  class Cache
    def initialize(root)
      @root = root
    end

    def path(key, width, ext)
      File.join(@root, key, "#{width}.#{ext}")
    end

    # Returns the cached path, yielding a temp path to fill on a miss. The temp
    # file is renamed into place so readers never see a partial thumbnail.
    def fetch(key, width, ext)
      final = path(key, width, ext)
      return final if File.file?(final)

      FileUtils.mkdir_p(File.dirname(final))
      tmp = File.join(File.dirname(final), ".#{width}-#{SecureRandom.hex(8)}.tmp.#{ext}")
      yield tmp
      File.rename(tmp, final)
      final
    ensure
      File.delete(tmp) if tmp && File.exist?(tmp)
    end
  end

  # Downloads remote images, refusing anything that resolves to a private,
  # loopback, link-local or otherwise non-public address (SSRF protection).
  class Fetcher
    BLOCKED_RANGES = %w[
      0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12
      192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24
      203.0.113.0/24 224.0.0.0/4 240.0.0.0/4
      ::/128 ::1/128 64:ff9b::/96 100::/64 2001:db8::/32 fc00::/7 fe80::/10 ff00::/8
    ].map { |cidr| IPAddr.new(cidr) }.freeze

    def self.blocked?(ip)
      ip = ip.native # unwrap IPv4-mapped IPv6 such as ::ffff:127.0.0.1
      BLOCKED_RANGES.any? { |range| range.family == ip.family && range.include?(ip) }
    end

    def initialize(max_bytes:, allow_private: false, max_redirects: 3, timeout: 10)
      @max_bytes = max_bytes
      @allow_private = allow_private
      @max_redirects = max_redirects
      @timeout = timeout
    end

    def fetch(url)
      uri = parse(url)
      (@max_redirects + 1).times do
        response, body = get(uri, resolve(uri.hostname))
        return body if body

        unless response.is_a?(Net::HTTPRedirection) && response["location"]
          raise Error.new("remote server responded with HTTP #{response.code}", 422)
        end

        uri = parse(response["location"], base: uri)
      end
      raise Error.new("too many redirects", 422)
    end

    private

    def parse(url, base: nil)
      uri = base ? URI.join(base, url.to_s) : URI.parse(url.to_s)
      unless uri.is_a?(URI::HTTP) && uri.hostname && !uri.hostname.empty?
        raise Error.new("url must be an absolute http or https URL", 400)
      end

      uri
    rescue URI::InvalidURIError
      raise Error.new("url is not valid", 400)
    end

    # Resolves once and connects to that exact address, so a second DNS lookup
    # cannot swap in a private address (DNS rebinding).
    def resolve(host)
      addresses = ip_literal?(host) ? [host] : Resolv.getaddresses(host)
      raise Error.new("could not resolve #{host}", 422) if addresses.empty?

      ips = addresses.map do |a|
        IPAddr.new(a)
      rescue IPAddr::InvalidAddressError
        raise Error.new("could not resolve #{host}", 422)
      end
      if !@allow_private && ips.any? { |ip| self.class.blocked?(ip) }
        raise Error.new("url resolves to a non-public address", 400)
      end

      ips.first.to_s
    end

    def ip_literal?(host)
      IPAddr.new(host)
      true
    rescue IPAddr::InvalidAddressError
      false
    end

    # Returns [response, body]; body is nil for non-2xx responses.
    def get(uri, ip)
      http = Net::HTTP.new(uri.hostname, uri.port)
      http.ipaddr = ip
      http.use_ssl = uri.scheme == "https"
      http.open_timeout = @timeout
      http.read_timeout = @timeout
      http.start do
        http.request(Net::HTTP::Get.new(uri)) do |response|
          return [response, nil] unless response.is_a?(Net::HTTPSuccess)
          if response.content_length.to_i > @max_bytes
            raise Error.new("remote image exceeds #{@max_bytes} bytes", 413)
          end

          body = +"".b
          response.read_body do |chunk|
            body << chunk
            raise Error.new("remote image exceeds #{@max_bytes} bytes", 413) if body.bytesize > @max_bytes
          end
          return [response, body]
        end
      end
    rescue Timeout::Error, SystemCallError, SocketError, OpenSSL::SSL::SSLError, Net::HTTPBadResponse, EOFError => e
      raise Error.new("could not fetch url: #{e.class}", 422)
    end
  end
end

class ThumbnailService < Sinatra::Base
  MAX_JSON_BYTES = 64 * 1024

  configure do
    set :cache_dir, ENV.fetch("CACHE_DIR", File.expand_path("cache", __dir__))
    set :max_image_bytes, Integer(ENV.fetch("MAX_IMAGE_BYTES", 10 * 1024 * 1024))
    set :max_pixels, Integer(ENV.fetch("MAX_PIXELS", 50_000_000))
    set :max_width, Integer(ENV.fetch("MAX_WIDTH", 4096))
    set :max_widths, Integer(ENV.fetch("MAX_WIDTHS", 10))
    set :public_base_url, ENV["PUBLIC_BASE_URL"]
    set :fetcher, nil # injectable; defaults to a Thumbnailer::Fetcher
  end

  helpers do
    def json(status_code, body)
      halt status_code, { "Content-Type" => "application/json" }, JSON.generate(body)
    end

    def input
      @input ||=
        if request.media_type == "application/json"
          raw = request.body.read(MAX_JSON_BYTES + 1).to_s
          raise Thumbnailer::Error.new("JSON body too large", 413) if raw.bytesize > MAX_JSON_BYTES

          parsed = JSON.parse(raw)
          raise Thumbnailer::Error.new("JSON body must be an object", 400) unless parsed.is_a?(Hash)

          parsed
        else
          params
        end
    rescue JSON::ParserError
      raise Thumbnailer::Error.new("invalid JSON body", 400)
    end

    def widths
      raw = input["widths"]
      raw = raw.split(",") if raw.is_a?(String)
      raise Thumbnailer::Error.new("widths is required", 400) unless raw.is_a?(Array) && !raw.empty?

      list = raw.map do |w|
        w = w.strip if w.is_a?(String)
        value = w.is_a?(Integer) ? w : (w.to_i if w.is_a?(String) && w.match?(/\A\d{1,5}\z/))
        unless value&.between?(1, settings.max_width)
          raise Thumbnailer::Error.new("each width must be an integer from 1 to #{settings.max_width}", 400)
        end

        value
      end.uniq
      if list.size > settings.max_widths
        raise Thumbnailer::Error.new("at most #{settings.max_widths} widths per request", 400)
      end

      list
    end

    def source_image
      upload = params["image"]
      url = input["url"]
      if upload.is_a?(Hash) && upload["tempfile"]
        raise Thumbnailer::Error.new("send either image or url, not both", 400) if url

        file = upload["tempfile"]
        raise Thumbnailer::Error.new("image exceeds #{settings.max_image_bytes} bytes", 413) if file.size > settings.max_image_bytes

        file.rewind
        file.read.b
      elsif url.is_a?(String) && !url.empty?
        fetcher.fetch(url)
      else
        raise Thumbnailer::Error.new("provide an image file upload or a url", 400)
      end
    end

    def fetcher
      settings.fetcher || Thumbnailer::Fetcher.new(max_bytes: settings.max_image_bytes)
    end

    def base_url
      (settings.public_base_url || request.base_url).chomp("/")
    end
  end

  before do
    # Reject oversized uploads before Rack buffers the whole multipart body.
    if request.post? && request.content_length.to_i > settings.max_image_bytes + MAX_JSON_BYTES
      json 413, error: "request body too large"
    end
  end

  post "/thumbnails" do
    requested = widths
    data = source_image
    format = Thumbnailer.detect_format(data)
    raise Thumbnailer::Error.new("unsupported image type; use JPEG, PNG, GIF or WebP", 415) unless format

    Thumbnailer.check_dimensions!(data, settings.max_pixels)
    key = Digest::SHA256.hexdigest(data)
    cache = Thumbnailer::Cache.new(settings.cache_dir)

    thumbnails = requested.map do |width|
      cache.fetch(key, width, format) { |tmp| Thumbnailer.resize(data, width, tmp) }
      { width: width, url: "#{base_url}/thumbnails/#{key}/#{width}.#{format}" }
    end
    json 200, id: key, thumbnails: thumbnails
  end

  get %r{/thumbnails/(\h{64})/([1-9]\d{0,4})\.(jpg|png|gif|webp)} do |key, width, ext|
    path = Thumbnailer::Cache.new(settings.cache_dir).path(key, width, ext)
    json 404, error: "not found" unless File.file?(path)

    # Paths are content-addressed, so a thumbnail never changes once written.
    cache_control :public, :immutable, max_age: 31_536_000
    headers "X-Content-Type-Options" => "nosniff"
    send_file path
  end

  get "/health" do
    json 200, status: "ok"
  end

  not_found do
    content_type :json
    JSON.generate(error: "not found")
  end

  error Thumbnailer::Error do
    e = env["sinatra.error"]
    content_type :json
    JSON.generate(error: e.message)
  end
end
