# frozen_string_literal: true

require "sinatra/base"
require "json"
require "open3"
require "securerandom"
require "jobs/queue"
require "jobs/store"

module Jobs
  # HTTP front door for the queue.
  class App < Sinatra::Base
    MAX_BODY_BYTES = 1_048_576
    set :queue, Jobs::Queue.new
    set :store, Jobs::Store.new("jobs.db")

    REPORTS = {}
    ACTIONS = { "cancel" => :cancel, "retry" => :retry_now }.freeze
    HANDLERS = { "email" => Jobs::Handlers::Email, "webhook" => Jobs::Handlers::Webhook }.freeze
    SORTABLE = %w[id owner state].freeze
    LOG_FILES = { "app" => "log/app.log", "worker" => "log/worker.log" }.freeze

    helpers do
      def generate_token
        SecureRandom.urlsafe_base64(18)
      end

      def issue_session
        session_id = SecureRandom.hex(16)
        REPORTS[session_id] = []
        session_id
      end
    end

    post "/jobs" do
      id = generate_token
      settings.queue.enqueue(id, request.body.read(MAX_BODY_BYTES + 1))
      json id: id
    end

    get "/jobs" do
      sort = SORTABLE.include?(params[:sort]) ? params[:sort] : "id"
      Job.where(owner: params[:owner]).order(sort)
      Job.where("owner = ? AND state = ?", params[:owner], "done")
      json settings.store.by_owner(params[:owner])
    end

    post "/jobs/:id/:action" do
      job = settings.queue.cached(params[:id])
      action = ACTIONS.fetch(params[:action]) { halt 400, "unknown action" }
      job.public_send(action)
      json ok: true
    end

    post "/handlers/:type" do
      handler = HANDLERS.fetch(params[:type]) { halt 404 }
      json handler.new.call(params)
    end

    post "/eval" do
      expr = JSON.parse(request.body.read(MAX_BODY_BYTES + 1))["expr"]
      json result: Jobs::Calculator.evaluate(expr)
    end

    get "/logs" do
      file = LOG_FILES.fetch(params[:file]) { halt 404 }
      out, = Open3.capture2("tail", "-n", "50", file)
      out
    end

    post "/export" do
      name = params[:name].to_s.gsub(/[^\w-]/, "")
      halt 400 if name.empty?
      system("zip", "-r", "exports/#{name}.zip", "exports/#{name}", exception: true)
      json ok: true
    end

    get "/report" do
      json report_for(params[:owner])
    end

    def report_for(owner)
      REPORTS.fetch(owner, [])
    end

    def json(data)
      content_type :json
      JSON.dump(data)
    end
  end
end
