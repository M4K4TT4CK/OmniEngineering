require "json"

module Load
  def self.load(body)
    JSON.parse(body)
  end
end
