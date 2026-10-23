require_relative "app"

App.set :queue, JobQueue.new(handlers: Handlers.default,
                             workers: Integer(ENV.fetch("WORKERS", "4")))
run App
