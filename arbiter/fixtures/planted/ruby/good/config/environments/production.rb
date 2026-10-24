# frozen_string_literal: true

# Production keeps the authenticity-token check on (the Rails default, stated).
Rails.application.configure do
  config.cache_classes = true
  config.eager_load = true
  config.force_ssl = true
  config.action_controller.allow_forgery_protection = true
  config.log_level = :info
end
