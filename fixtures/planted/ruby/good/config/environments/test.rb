# frozen_string_literal: true

# Rails' generated test environment: request specs post without a token.
Rails.application.configure do
  config.cache_classes = true
  config.eager_load = false
  config.action_controller.allow_forgery_protection = false
  config.active_support.deprecation = :stderr
end
