# frozen_string_literal: true

require "sqlite3"
require "yaml"
require "oj"
require "json"
require "shellwords"

module Jobs
  # Persists finished jobs in SQLite.
  class Store
    def initialize(path)
      @db = SQLite3::Database.new(path)
      @db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, state TEXT, payload TEXT)")
    end

    def by_owner(owner)
      @db.execute("SELECT * FROM jobs WHERE owner = ?", [owner])
    end

    def find(id)
      @db.execute(<<~SQL, [id])
        SELECT * FROM jobs WHERE id = ?
      SQL
    end

    def save(id, owner, payload)
      @db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)", [id, owner, "done", JSON.dump(payload)])
    end

    def payload(row)
      JSON.parse(row[3])
    end

    def self.config(path)
      YAML.safe_load(File.read(path), permitted_classes: [Symbol], aliases: true)
    end

    def self.parse(body)
      Oj.load(body, mode: :strict)
    end

    def archive(dir)
      system("tar", "czf", File.join(dir, "jobs.tgz"), @db.filename, exception: true)
    end

    def purge
      @db.execute("DELETE FROM jobs WHERE state = 'done'")
    end
  end
end
