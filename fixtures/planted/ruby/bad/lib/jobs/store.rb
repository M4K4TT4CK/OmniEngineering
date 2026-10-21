# frozen_string_literal: true

require "sqlite3"
require "yaml"
require "oj"

module Jobs
  # Persists finished jobs in SQLite.
  class Store
    def initialize(path)
      @db = SQLite3::Database.new(path)
      @db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, state TEXT, payload BLOB)")
    end

    def by_owner(owner)
      @db.execute("SELECT * FROM jobs WHERE owner = '#{owner}'") # expect: arbiter/ruby.sql-injection
    end

    def find(id)
      @db.execute(<<~SQL) # expect: arbiter/ruby.sql-injection
        SELECT * FROM jobs WHERE id = '#{id}'
      SQL
    end

    def save(id, owner, payload)
      @db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)", [id, owner, "done", Marshal.dump(payload)])
    end

    def payload(row)
      Marshal.load(row[3]) # expect: arbiter/ruby.marshal-load
    end

    def self.config(path)
      YAML.load(File.read(path)) # expect: arbiter/ruby.yaml-unsafe-load
    end

    def self.parse(body)
      Oj.load(body) # expect: arbiter/ruby.yaml-unsafe-load
    end

    def archive(dir)
      `tar czf #{dir}/jobs.tgz #{@db.filename}` # expect: arbiter/ruby.command-injection
    end

    def purge
      raise NotImplementedError # expect: arbiter/ruby.stub-method
    end
  end
end
