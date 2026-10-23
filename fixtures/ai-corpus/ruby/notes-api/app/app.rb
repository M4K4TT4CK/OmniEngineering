require 'sinatra/base'
require 'sqlite3'
require 'bcrypt'
require 'json'
require 'securerandom'
require 'digest'
require 'time'

# Thin SQLite wrapper. A single connection is shared and guarded by a mutex.
class Database
  def initialize(path)
    @db = SQLite3::Database.new(path)
    @db.results_as_hash = true
    @db.busy_timeout = 5000
    @db.execute('PRAGMA foreign_keys = ON')
    @mutex = Mutex.new
    migrate
  end

  def synchronize(&block)
    @mutex.synchronize(&block)
  end

  def execute(sql, params = [])
    synchronize { @db.execute(sql, params) }
  end

  def get(sql, params = [])
    execute(sql, params).first
  end

  def insert(sql, params = [])
    synchronize do
      @db.execute(sql, params)
      @db.last_insert_row_id
    end
  end

  def changes
    @db.changes
  end

  # Fixed-window counter; returns the number of hits in the current window.
  def hit_rate_limit(key, window)
    synchronize do
      @db.execute('DELETE FROM rate_limits WHERE window < ?', [window])
      @db.execute(<<~SQL, [key, window])
        INSERT INTO rate_limits (key, window, count) VALUES (?, ?, 1)
        ON CONFLICT (key, window) DO UPDATE SET count = count + 1
      SQL
      @db.get_first_value('SELECT count FROM rate_limits WHERE key = ? AND window = ?', [key, window])
    end
  end

  # Runs a statement and returns the affected row count atomically.
  def run(sql, params = [])
    synchronize do
      @db.execute(sql, params)
      @db.changes
    end
  end

  private

  def migrate
    @db.execute_batch(<<~SQL)
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_digest TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS tokens (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
      CREATE TABLE IF NOT EXISTS rate_limits (
        key TEXT NOT NULL,
        window INTEGER NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (key, window)
      );
    SQL
  end
end

class NotesApp < Sinatra::Base
  RATE_LIMIT = 60
  RATE_WINDOW = 60                 # seconds
  TOKEN_TTL = 7 * 24 * 3600        # seconds
  USERNAME_RE = /\A[A-Za-z0-9_]{3,32}\z/
  MAX_TITLE = 200
  MAX_BODY = 10_000
  # Used to equalise timing when the username does not exist.
  DUMMY_DIGEST = BCrypt::Password.create('dummy-password')

  set :show_exceptions, false
  set :raise_errors, false
  set :clock, -> { Time.now }
  set :db, nil

  def self.database
    settings.db ||= Database.new(ENV.fetch('NOTES_DB', 'notes.db'))
  end

  helpers do
    def db = NotesApp.database
    def now = settings.clock.call

    def json(status_code, payload)
      status status_code
      content_type :json
      JSON.generate(payload)
    end

    def fail!(status_code, message)
      halt json(status_code, error: message)
    end

    def params_json
      @params_json ||= begin
        data = JSON.parse(request.body.read)
        fail!(400, 'JSON body must be an object') unless data.is_a?(Hash)
        data
      rescue JSON::ParserError
        fail!(400, 'invalid JSON')
      end
    end

    def token_hash(token) = Digest::SHA256.hexdigest(token)

    def current_user
      return @current_user if defined?(@current_user)

      header = request.env['HTTP_AUTHORIZATION'].to_s
      token = header[/\ABearer (\S+)\z/, 1]
      @current_user = token && db.get(<<~SQL, [token_hash(token), now.to_i])
        SELECT users.id, users.username FROM tokens
        JOIN users ON users.id = tokens.user_id
        WHERE tokens.token_hash = ? AND tokens.expires_at > ?
      SQL
    end

    def require_user!
      fail!(401, 'missing or invalid token') unless current_user
      current_user
    end

    def note_json(row)
      row.slice('id', 'title', 'body', 'created_at', 'updated_at')
    end

    def validate_title(title)
      unless title.is_a?(String) && !title.strip.empty? && title.length <= MAX_TITLE
        fail!(422, "title must be a non-empty string of at most #{MAX_TITLE} characters")
      end
    end

    def validate_body(body)
      fail!(422, "body must be a string of at most #{MAX_BODY} characters") unless body.is_a?(String) && body.length <= MAX_BODY
    end

    def find_note(id)
      require_user!
      note = db.get('SELECT * FROM notes WHERE id = ? AND user_id = ?', [id.to_i, current_user['id']])
      fail!(404, 'note not found') unless note
      note
    end
  end

  # Rate limit per authenticated user, or per client IP for anonymous requests.
  before do
    key = current_user ? "user:#{current_user['id']}" : "ip:#{request.ip}"
    window = now.to_i / RATE_WINDOW
    count = db.hit_rate_limit(key, window)
    headers 'X-RateLimit-Limit' => RATE_LIMIT.to_s,
            'X-RateLimit-Remaining' => [RATE_LIMIT - count, 0].max.to_s
    if count > RATE_LIMIT
      retry_after = (window + 1) * RATE_WINDOW - now.to_i
      headers 'Retry-After' => retry_after.to_s
      fail!(429, 'rate limit exceeded')
    end
  end

  post '/signup' do
    username = params_json['username']
    password = params_json['password']
    fail!(422, 'username must be 3-32 characters: letters, digits, underscore') unless username.is_a?(String) && username.match?(USERNAME_RE)
    # bcrypt only uses the first 72 bytes.
    fail!(422, 'password must be 8-72 bytes') unless password.is_a?(String) && (8..72).cover?(password.bytesize)

    begin
      id = db.insert('INSERT INTO users (username, password_digest, created_at) VALUES (?, ?, ?)',
                     [username, BCrypt::Password.create(password).to_s, now.utc.iso8601])
    rescue SQLite3::ConstraintException
      fail!(409, 'username already taken')
    end
    json 201, id: id, username: username
  end

  post '/login' do
    username = params_json['username']
    password = params_json['password']
    user = username.is_a?(String) ? db.get('SELECT * FROM users WHERE username = ?', [username]) : nil
    digest = BCrypt::Password.new(user ? user['password_digest'] : DUMMY_DIGEST)
    ok = password.is_a?(String) && digest == password
    fail!(401, 'invalid username or password') unless user && ok

    token = SecureRandom.urlsafe_base64(32)
    expires_at = now.to_i + TOKEN_TTL
    # Only a hash of the token is stored.
    db.execute('INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)',
               [token_hash(token), user['id'], expires_at])
    db.execute('DELETE FROM tokens WHERE expires_at <= ?', [now.to_i])
    json 200, token: token, expires_at: Time.at(expires_at).utc.iso8601
  end

  post '/notes' do
    user = require_user!
    title = params_json['title']
    body = params_json.fetch('body', '')
    validate_title(title)
    validate_body(body)
    ts = now.utc.iso8601
    id = db.insert('INSERT INTO notes (user_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
                   [user['id'], title, body, ts, ts])
    json 201, note_json(db.get('SELECT * FROM notes WHERE id = ?', [id]))
  end

  get '/notes' do
    user = require_user!
    limit = params['limit'] ? params['limit'].to_i.clamp(1, 100) : 20
    offset = [params['offset'].to_i, 0].max
    sql = 'SELECT * FROM notes WHERE user_id = ?'
    args = [user['id']]
    q = params['q'].to_s.strip
    unless q.empty?
      pattern = "%#{q.gsub(/[\\%_]/) { |c| "\\#{c}" }}%"
      sql += " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
      args += [pattern, pattern]
    end
    rows = db.execute("#{sql} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?", args + [limit, offset])
    json 200, notes: rows.map { |r| note_json(r) }, limit: limit, offset: offset
  end

  get '/notes/:id' do
    json 200, note_json(find_note(params[:id]))
  end

  put '/notes/:id' do
    note = find_note(params[:id])
    data = params_json
    fail!(422, 'provide title and/or body') unless data.key?('title') || data.key?('body')
    title = data.fetch('title', note['title'])
    body = data.fetch('body', note['body'])
    validate_title(title)
    validate_body(body)
    db.execute('UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND user_id = ?',
               [title, body, now.utc.iso8601, note['id'], current_user['id']])
    json 200, note_json(db.get('SELECT * FROM notes WHERE id = ?', [note['id']]))
  end

  delete '/notes/:id' do
    note = find_note(params[:id])
    db.execute('DELETE FROM notes WHERE id = ? AND user_id = ?', [note['id'], current_user['id']])
    status 204
    nil
  end

  not_found do
    content_type :json
    JSON.generate(error: 'not found')
  end

  error do
    content_type :json
    status 500
    JSON.generate(error: 'internal server error')
  end

  run! if app_file == $PROGRAM_NAME
end
