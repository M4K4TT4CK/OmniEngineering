# Request bodies read with limits (ARB-083).
require "json"
require "zlib"

module Ingest
  MAX_BODY_BYTES = 1_048_576
  MAX_INFLATED = 16 * MAX_BODY_BYTES

  def self.parse(request)
    raise ArgumentError, "body too large" if request.content_length.to_i > MAX_BODY_BYTES

    raw = request.body.read(MAX_BODY_BYTES + 1).to_s
    raise ArgumentError, "body too large" if raw.bytesize > MAX_BODY_BYTES

    JSON.parse(raw)
  end

  def self.inflate(request)
    out = +""
    inflater = Zlib::Inflate.new(Zlib::MAX_WBITS + 32)
    inflater.inflate(request.body.read(MAX_BODY_BYTES).to_s) do |chunk|
      out << chunk
      raise ArgumentError, "inflated body too large" if out.bytesize > MAX_INFLATED
    end
    out
  end
end
