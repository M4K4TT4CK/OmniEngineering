require 'sqlite3'
require_relative 'app'

ReportApp.set :db, SQLite3::Database.new(ENV.fetch('DATABASE_PATH', 'shop.db'), results_as_hash: true)
ReportApp.set :api_keys, ENV.fetch('ADMIN_API_KEYS', '').split(',').map(&:strip).reject(&:empty?)

run ReportApp
