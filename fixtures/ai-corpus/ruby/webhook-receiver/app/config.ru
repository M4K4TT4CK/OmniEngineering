require_relative "app"

secret = ENV.fetch("WEBHOOK_SECRET") { abort "WEBHOOK_SECRET is required" }
admin_token = ENV.fetch("ADMIN_TOKEN") { abort "ADMIN_TOKEN is required" }

store = Store.new(ENV.fetch("DATABASE_PATH", "webhooks.db"))

# MERCHANTS_FILE: JSON object mapping merchant id => callback URL.
if (file = ENV["MERCHANTS_FILE"])
  JSON.parse(File.read(file)).each { |id, url| store.upsert_merchant(id, url) }
end

WebhookApp.set :store, store
WebhookApp.set :secret, secret
WebhookApp.set :admin_token, admin_token

run WebhookApp
