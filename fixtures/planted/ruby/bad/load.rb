module Load
  def self.load(body)
    Marshal.load(body) # expect: arbiter/ruby.marshal-load
  end
end
