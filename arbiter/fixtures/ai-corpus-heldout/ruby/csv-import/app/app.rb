require "fileutils"
require "json"
require "securerandom"
require "sinatra/base"
require_relative "importer"

module CsvImport
  class App < Sinatra::Base
    # Import id => background thread. Kept in memory, so it does not survive a restart.
    JOBS = {}
    JOBS_LOCK = Mutex.new
    IMPORT_FIELDS = %w[id status total_rows processed_rows inserted updated failed fatal_error created_at finished_at].freeze

    set :database_path, ENV.fetch("CSV_IMPORT_DB", "customers.sqlite3")
    set :uploads_dir, ENV.fetch("CSV_IMPORT_UPLOADS", "uploads")

    # Accepts a multipart upload in field "file" and starts a background import.
    post "/imports" do
      upload = params["file"]
      halt 400, render_json(error: "send the CSV as multipart field 'file'") unless upload.is_a?(Hash) && upload[:tempfile]

      id = SecureRandom.uuid
      FileUtils.mkdir_p(settings.uploads_dir)
      csv_path = File.join(settings.uploads_dir, "#{id}.csv")
      FileUtils.cp(upload[:tempfile].path, csv_path)
      CsvImport.create_import(settings.database_path, id)
      start_job(id, csv_path)

      status 202
      render_json(id: id, status_url: "/imports/#{id}")
    end

    # Reports progress and per-row errors. Errors are paged with ?limit= and ?offset=.
    get "/imports/:id" do
      limit = int_param("limit", default: 100, max: 1000)
      offset = int_param("offset", default: 0, max: 1_000_000_000)
      with_db do |db|
        import = db.get_first_row("SELECT * FROM imports WHERE id = ?", [params[:id]])
        halt 404, render_json(error: "import not found") unless import

        errors = db.execute(
          "SELECT line, message FROM import_errors WHERE import_id = ? ORDER BY line LIMIT ? OFFSET ?",
          [params[:id], limit, offset]
        )
        total = db.get_first_value("SELECT COUNT(*) FROM import_errors WHERE import_id = ?", [params[:id]])
        render_json(
          import.slice(*IMPORT_FIELDS).merge(
            errors_total: total,
            errors: errors.map { |e| e.slice("line", "message") }
          )
        )
      end
    end

    private

    def start_job(id, csv_path)
      db_path = settings.database_path
      JOBS_LOCK.synchronize do
        JOBS[id] = Thread.new { Importer.new(db_path, id, csv_path).run }
      end
    end

    def with_db
      db = CsvImport.open_db(settings.database_path)
      yield db
    ensure
      db&.close
    end

    def int_param(name, default:, max:)
      value = Integer(params.fetch(name, default).to_s, exception: false) || default
      value.clamp(0, max)
    end

    def render_json(payload)
      content_type :json
      JSON.pretty_generate(payload)
    end
  end
end

CsvImport::App.run! if __FILE__ == $PROGRAM_NAME
