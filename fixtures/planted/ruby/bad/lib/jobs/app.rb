# frozen_string_literal: true

require "sinatra/base"
require "json"
require "open3"
require "jobs/queue"
require "jobs/store"

module Jobs
  # HTTP front door for the queue.
  class App < Sinatra::Base
    set :queue, Jobs::Queue.new
    set :store, Jobs::Store.new("jobs.db")

    REPORTS = {}

    helpers do
      def generate_token
        rand(36**12).to_s(36) # expect: arbiter/ruby.insecure-random
      end

      def issue_session
        session_id = Random.new.bytes(16).unpack1("H*") # expect: arbiter/ruby.insecure-random
        REPORTS[session_id] = []
        session_id
      end
    end

    post "/jobs" do
      id = generate_token
      settings.queue.enqueue(id, request.body.read)
      json id: id
    end

    get "/jobs" do
      rows = settings.store.by_owner(params[:owner])
      rows = rows.sort_by { |r| r[params[:sort]] }
      Job.where("owner = '#{params[:owner]}'").order(params[:sort]) # expect: arbiter/ruby.sql-injection
      json rows
    end

    post "/jobs/:id/:action" do
      job = settings.queue.cached(params[:id])
      job.send(params[:action]) # expect: arbiter/ruby.code-injection
      json ok: true
    end

    post "/handlers/:type" do
      handler = "Jobs::Handlers::#{params[:type]}".constantize # expect: arbiter/ruby.code-injection
      json handler.new.call(params)
    end

    post "/eval" do
      expr = JSON.parse(request.body.read)["expr"]
      json result: eval(expr) # expect: arbiter/ruby.code-injection
    end

    get "/logs" do
      out, = Open3.capture2("tail -n 50 #{params[:file]}") # expect: arbiter/ruby.command-injection
      out
    end

    post "/export" do
      system("zip -r exports/#{params[:name]}.zip exports/#{params[:name]}") # expect: arbiter/ruby.command-injection
      json ok: true
    end

    get "/report" do
      json report_for(params[:owner])
    end

    def report_for(owner)
      nil # TODO: aggregate per owner # expect: arbiter/ruby.stub-method
    end

    def json(data)
      content_type :json
      JSON.dump(data)
    end
  end
end
