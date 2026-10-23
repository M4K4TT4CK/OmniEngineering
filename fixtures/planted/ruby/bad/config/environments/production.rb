# frozen_string_literal: true

# CWE-352: the test environment's setting copied into production to make an
# InvalidAuthenticityToken error go away; no controller checks the token now.
Rails.application.configure do
  config.cache_classes = true
  config.eager_load = true
  config.force_ssl = true
  config.action_controller.allow_forgery_protection = false # expect: arbiter/ruby.csrf-protection-off-in-production
  config.log_level = :info
end
