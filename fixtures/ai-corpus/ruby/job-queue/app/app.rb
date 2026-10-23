require "sinatra/base"
require "json"
require_relative "job_queue"

class App < Sinatra::Base
  set :queue, nil
  set :show_exceptions, false

  before { content_type :json }

  # POST /jobs  {"type": "send-email", "payload": {...}}  ->  202 {"id": "..."}
  post "/jobs" do
    body = begin
      JSON.parse(request.body.read)
    rescue JSON::ParserError
      halt 400, { error: "invalid JSON" }.to_json
    end
    halt 400, { error: "body must be a JSON object" }.to_json unless body.is_a?(Hash)

    begin
      id = settings.queue.enqueue(body["type"], body["payload"])
      status 202
      { id: id }.to_json
    rescue ArgumentError => e # includes JobQueue::UnknownJobType
      halt 422, { error: e.message }.to_json
    end
  end

  # GET /jobs/:id  ->  status, attempts, result, error
  get "/jobs/:id" do
    job = settings.queue.status(params[:id])
    halt 404, { error: "job not found" }.to_json unless job
    job.slice(:id, :type, :status, :attempts, :result, :error, :created_at, :updated_at).to_json
  end
end
