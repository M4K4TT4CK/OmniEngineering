# Request bodies as model-written Sinatra services read them (ARB-083).
require "json"
require "zlib"

module Ingest
  def self.parse(request)
    JSON.parse(request.body.read) # expect: arbiter/ruby.request-body-read-unbounded
  end

  def self.inflate(request)
    Zlib.gunzip(request.body.read(1_048_576).to_s) # expect: arbiter/ruby.decompress-unbounded
  end
end
