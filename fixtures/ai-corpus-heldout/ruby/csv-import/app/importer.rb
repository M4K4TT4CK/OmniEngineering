require "csv"
require "date"
require "sqlite3"
require "time"

module CsvImport
  class ImportError < StandardError; end

  HEADERS = %w[name email phone country signup_date].freeze
  OUTCOMES = %w[inserted updated failed].freeze
  EMAIL_PATTERN = /\A[^@\s]+@[^@\s]+\.[^@\s]+\z/
  PHONE_PATTERN = /\A\+?[0-9 ()\-.]{4,30}\z/
  COUNTRY_PATTERN = /\A[A-Z]{2}\z/
  DATE_PATTERN = /\A\d{4}-\d{2}-\d{2}\z/

  SCHEMA = <<~SQL
    CREATE TABLE IF NOT EXISTS customers (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE,
      phone TEXT,
      country TEXT NOT NULL,
      signup_date TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS imports (
      id TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      total_rows INTEGER NOT NULL DEFAULT 0,
      processed_rows INTEGER NOT NULL DEFAULT 0,
      inserted INTEGER NOT NULL DEFAULT 0,
      updated INTEGER NOT NULL DEFAULT 0,
      failed INTEGER NOT NULL DEFAULT 0,
      fatal_error TEXT,
      created_at TEXT NOT NULL,
      finished_at TEXT
    );
    CREATE TABLE IF NOT EXISTS import_errors (
      id INTEGER PRIMARY KEY,
      import_id TEXT NOT NULL REFERENCES imports(id),
      line INTEGER NOT NULL,
      message TEXT NOT NULL
    );
  SQL

  UPSERT_SQL = <<~SQL
    INSERT INTO customers (name, email, phone, country, signup_date, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(email) DO UPDATE SET
      name = excluded.name,
      phone = excluded.phone,
      country = excluded.country,
      signup_date = excluded.signup_date,
      updated_at = excluded.updated_at
  SQL

  def self.open_db(path)
    db = SQLite3::Database.new(path)
    db.results_as_hash = true
    db.busy_timeout = 5_000
    db.execute("PRAGMA journal_mode = WAL")
    db.execute_batch(SCHEMA)
    db
  end

  def self.now
    Time.now.utc.iso8601
  end

  def self.create_import(db_path, id)
    db = open_db(db_path)
    db.execute("INSERT INTO imports (id, status, created_at) VALUES (?, 'queued', ?)", [id, now])
  ensure
    db&.close
  end

  # Normalises one CSV row. Returns [customer, []] when valid, otherwise [nil, messages].
  def self.validate_row(row)
    name = row["name"].to_s.strip
    email = row["email"].to_s.strip.downcase
    phone = row["phone"].to_s.strip
    country = row["country"].to_s.strip.upcase
    signup_date = parse_date(row["signup_date"].to_s.strip)

    errors = []
    errors << "name is required" if name.empty?
    errors << "email is invalid" unless email.match?(EMAIL_PATTERN)
    errors << "phone is invalid" unless phone.empty? || phone.match?(PHONE_PATTERN)
    errors << "country must be a 2-letter code" unless country.match?(COUNTRY_PATTERN)
    errors << "signup_date must be YYYY-MM-DD" unless signup_date
    return [nil, errors] unless errors.empty?

    customer = {
      name: name,
      email: email,
      phone: phone.empty? ? nil : phone,
      country: country,
      signup_date: signup_date.iso8601
    }
    [customer, []]
  end

  def self.parse_date(text)
    return nil unless text.match?(DATE_PATTERN)

    Date.strptime(text, "%Y-%m-%d")
  rescue ArgumentError
    nil
  end

  class Importer
    def initialize(db_path, import_id, csv_path)
      @db_path = db_path
      @import_id = import_id
      @csv_path = csv_path
    end

    # Runs the whole import. Structural problems (bad header, malformed CSV) fail the
    # import; bad values are recorded per row and the import continues.
    def run
      @db = CsvImport.open_db(@db_path)
      @db.execute("UPDATE imports SET status = 'running' WHERE id = ?", [@import_id])

      headers, rows = read_rows
      missing = HEADERS - Array(headers)
      raise ImportError, "missing columns: #{missing.join(", ")}" unless missing.empty?

      @db.execute("UPDATE imports SET total_rows = ? WHERE id = ?", [rows.length, @import_id])
      rows.each { |line, row| process(line, row) }
      finish("completed")
    rescue StandardError => e
      finish("failed", e.message)
    ensure
      @db&.close
    end

    private

    def read_rows
      rows = []
      headers = nil
      CSV.open(@csv_path, headers: true) do |csv|
        csv.each { |row| rows << [csv.lineno, row] }
        headers = csv.headers
      end
      [headers, rows]
    end

    def process(line, row)
      customer, errors = CsvImport.validate_row(row)
      if errors.empty?
        outcome = upsert(customer)
      else
        outcome = "failed"
        @db.execute(
          "INSERT INTO import_errors (import_id, line, message) VALUES (?, ?, ?)",
          [@import_id, line, errors.join("; ")]
        )
      end
      raise ArgumentError, "unknown outcome #{outcome}" unless OUTCOMES.include?(outcome)

      @db.execute(
        "UPDATE imports SET processed_rows = processed_rows + 1, #{outcome} = #{outcome} + 1 WHERE id = ?",
        [@import_id]
      )
    end

    # Inserts or updates by email. The check and write share one immediate transaction
    # so the inserted/updated count stays accurate if two imports overlap.
    def upsert(customer)
      outcome = nil
      @db.transaction(:immediate) do
        existing = @db.get_first_value("SELECT id FROM customers WHERE email = ?", [customer[:email]])
        @db.execute(
          UPSERT_SQL,
          [customer[:name], customer[:email], customer[:phone], customer[:country], customer[:signup_date], CsvImport.now]
        )
        outcome = existing ? "updated" : "inserted"
      end
      outcome
    end

    def finish(status, fatal_error = nil)
      @db.execute(
        "UPDATE imports SET status = ?, fatal_error = ?, finished_at = ? WHERE id = ?",
        [status, fatal_error, CsvImport.now, @import_id]
      )
    end
  end
end
