require 'sinatra/base'
require 'sqlite3'
require 'bcrypt'
require 'json'
require 'securerandom'
require 'digest'
require 'time'

class NotesApp < Sinatra::Base
  ROLES = %w[admin editor viewer].freeze
  WRITE_ROLES = %w[admin editor].freeze
  TOKEN_TTL = 24 * 60 * 60
  INVITE_TTL = 7 * 24 * 60 * 60
  MAX_TITLE = 200
  MAX_BODY = 100_000
  MAX_QUERY = 200
  EMAIL_RE = /\A[^@\s]+@[^@\s]+\.[^@\s]+\z/
  # Compared against when the email is unknown, so login takes the same time
  # whether or not the account exists.
  DUMMY_DIGEST = BCrypt::Password.create(SecureRandom.hex(16))

  SCHEMA = <<~SQL
    CREATE TABLE IF NOT EXISTS organizations (
      id         INTEGER PRIMARY KEY,
      name       TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS users (
      id              INTEGER PRIMARY KEY,
      organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
      password_digest TEXT NOT NULL,
      role            TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
      created_at      TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS auth_tokens (
      token_digest TEXT PRIMARY KEY,
      user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at   INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invitations (
      id              INTEGER PRIMARY KEY,
      organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      email           TEXT NOT NULL COLLATE NOCASE,
      role            TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
      token_digest    TEXT NOT NULL UNIQUE,
      invited_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
      expires_at      INTEGER NOT NULL,
      accepted_at     TEXT
    );
    CREATE TABLE IF NOT EXISTS notes (
      id              INTEGER PRIMARY KEY,
      organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      author_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
      title           TEXT NOT NULL,
      body            TEXT NOT NULL,
      created_at      TEXT NOT NULL,
      updated_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS notes_org_idx ON notes (organization_id, updated_at);
  SQL

  def self.connect(path)
    db = SQLite3::Database.new(path, results_as_hash: true)
    db.busy_timeout = 5000
    db.execute('PRAGMA foreign_keys = ON')
    db.execute_batch(SCHEMA)
    set :db, db
  end

  configure do
    # One SQLite connection is shared, so requests are serialised.
    set :lock, true
    set :show_exceptions, false
    set :raise_errors, false
    connect(ENV.fetch('DATABASE_PATH', 'notes.db'))
  end

  helpers do
    def db
      settings.db
    end

    def now
      Time.now.utc.iso8601
    end

    def digest(token)
      Digest::SHA256.hexdigest(token)
    end

    def json_response(status_code, payload)
      status status_code
      content_type :json
      JSON.generate(payload)
    end

    def halt_json(status_code, message)
      halt status_code, { 'Content-Type' => 'application/json' }, JSON.generate(error: message)
    end

    def json_body
      @json_body ||= begin
        data = JSON.parse(request.body.read)
        halt_json(400, 'request body must be a JSON object') unless data.is_a?(Hash)
        data
      rescue JSON::ParserError
        halt_json(400, 'invalid JSON')
      end
    end

    def fetch_string(data, key, max:, required: true)
      value = data[key]
      if value.nil?
        halt_json(422, "#{key} is required") if required
        return nil
      end
      halt_json(422, "#{key} must be a string") unless value.is_a?(String)
      halt_json(422, "#{key} must be at most #{max} characters") if value.length > max
      value
    end

    def fetch_title(data, required: true)
      title = fetch_string(data, 'title', max: MAX_TITLE, required: required)
      return nil if title.nil?

      title = title.strip
      halt_json(422, 'title must not be blank') if title.empty?
      title
    end

    def fetch_email(data)
      email = fetch_string(data, 'email', max: 254).strip.downcase
      halt_json(422, 'email is invalid') unless email.match?(EMAIL_RE)
      email
    end

    def fetch_password(data)
      password = fetch_string(data, 'password', max: 1000)
      halt_json(422, 'password must be at least 8 characters') if password.length < 8
      # bcrypt only uses the first 72 bytes.
      halt_json(422, 'password must be at most 72 bytes') if password.bytesize > 72
      password
    end

    def fetch_role(data)
      role = data['role']
      halt_json(422, "role must be one of #{ROLES.join(', ')}") unless ROLES.include?(role)
      role
    end

    def int_param(name, default:, min:, max:)
      raw = params[name]
      return default if raw.nil?
      halt_json(422, "#{name} must be a non-negative integer") unless raw.is_a?(String) && raw.match?(/\A\d{1,9}\z/)
      raw.to_i.clamp(min, max)
    end

    def escape_like(text)
      text.gsub(/[\\%_]/) { |c| "\\#{c}" }
    end

    def email_taken?(email)
      !db.get_first_value('SELECT 1 FROM users WHERE email = ?', [email]).nil?
    end

    def issue_token(user_id)
      token = SecureRandom.urlsafe_base64(32)
      db.execute('INSERT INTO auth_tokens (token_digest, user_id, expires_at) VALUES (?, ?, ?)',
                 [digest(token), user_id, Time.now.to_i + TOKEN_TTL])
      token
    end

    def bearer_token
      request.env['HTTP_AUTHORIZATION'].to_s[/\ABearer ([A-Za-z0-9_\-]+)\z/, 1]
    end

    def current_user
      @current_user ||= begin
        token = bearer_token
        halt_json(401, 'authentication required') unless token
        user = db.get_first_row(<<~SQL, [digest(token), Time.now.to_i])
          SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id
          WHERE t.token_digest = ? AND t.expires_at > ?
        SQL
        halt_json(401, 'invalid or expired token') unless user
        user
      end
    end

    def require_role!(*roles)
      halt_json(403, 'forbidden') unless roles.include?(current_user['role'])
    end

    def find_in_org!(table, id)
      halt_json(404, 'not found') unless id.match?(/\A\d{1,18}\z/)
      row = db.get_first_row("SELECT * FROM #{table} WHERE id = ? AND organization_id = ?",
                             [id.to_i, current_user['organization_id']])
      row || halt_json(404, 'not found')
    end

    def user_json(user)
      { id: user['id'], organization_id: user['organization_id'], email: user['email'], role: user['role'] }
    end

    def note_json(note)
      note.slice('id', 'title', 'body', 'author_id', 'created_at', 'updated_at')
    end
  end

  not_found do
    json_response(404, error: 'not found')
  end

  error SQLite3::ConstraintException do
    json_response(409, error: 'conflict')
  end

  error 500 do
    json_response(500, error: 'internal server error')
  end

  # --- Accounts -------------------------------------------------------------

  # Creates a new organisation with the caller as its first admin.
  post '/signup' do
    data = json_body
    org_name = fetch_string(data, 'organization', max: 100).strip
    halt_json(422, 'organization must not be blank') if org_name.empty?
    email = fetch_email(data)
    password = fetch_password(data)
    halt_json(409, 'email already registered') if email_taken?(email)

    user = token = nil
    db.transaction do
      db.execute('INSERT INTO organizations (name, created_at) VALUES (?, ?)', [org_name, now])
      org_id = db.last_insert_row_id
      db.execute('INSERT INTO users (organization_id, email, password_digest, role, created_at) VALUES (?, ?, ?, ?, ?)',
                 [org_id, email, BCrypt::Password.create(password), 'admin', now])
      user = db.get_first_row('SELECT * FROM users WHERE id = ?', [db.last_insert_row_id])
      token = issue_token(user['id'])
    end
    json_response(201, token: token, expires_in: TOKEN_TTL, user: user_json(user))
  end

  post '/login' do
    data = json_body
    email = data['email'].is_a?(String) ? data['email'].strip.downcase : ''
    password = data['password'].is_a?(String) ? data['password'] : ''
    user = db.get_first_row('SELECT * FROM users WHERE email = ?', [email])
    stored = BCrypt::Password.new(user ? user['password_digest'] : DUMMY_DIGEST)
    halt_json(401, 'invalid email or password') unless stored.is_password?(password) && user

    db.execute('DELETE FROM auth_tokens WHERE expires_at <= ?', [Time.now.to_i])
    json_response(200, token: issue_token(user['id']), expires_in: TOKEN_TTL, user: user_json(user))
  end

  delete '/session' do
    current_user
    db.execute('DELETE FROM auth_tokens WHERE token_digest = ?', [digest(bearer_token)])
    status 204
  end

  get '/me' do
    json_response(200, user: user_json(current_user))
  end

  # --- Users and invitations ------------------------------------------------

  get '/users' do
    require_role!('admin')
    users = db.execute('SELECT * FROM users WHERE organization_id = ? ORDER BY id',
                       [current_user['organization_id']])
    json_response(200, users: users.map { |u| user_json(u) })
  end

  # The invitation token is returned to the admin to pass on; this service
  # does not send email.
  post '/invitations' do
    require_role!('admin')
    data = json_body
    email = fetch_email(data)
    role = fetch_role(data)
    halt_json(409, 'email already registered') if email_taken?(email)

    token = SecureRandom.urlsafe_base64(32)
    expires_at = Time.now.to_i + INVITE_TTL
    db.execute(<<~SQL, [current_user['organization_id'], email, role, digest(token), current_user['id'], expires_at])
      INSERT INTO invitations (organization_id, email, role, token_digest, invited_by, expires_at)
      VALUES (?, ?, ?, ?, ?, ?)
    SQL
    json_response(201, invitation: { id: db.last_insert_row_id, email: email, role: role,
                                     expires_at: Time.at(expires_at).utc.iso8601 },
                       token: token)
  end

  post '/invitations/accept' do
    data = json_body
    token = fetch_string(data, 'token', max: 200)
    password = fetch_password(data)
    invitation = db.get_first_row(<<~SQL, [digest(token), Time.now.to_i])
      SELECT * FROM invitations WHERE token_digest = ? AND accepted_at IS NULL AND expires_at > ?
    SQL
    halt_json(404, 'not found') unless invitation
    halt_json(409, 'email already registered') if email_taken?(invitation['email'])

    user = session_token = nil
    db.transaction do
      db.execute('UPDATE invitations SET accepted_at = ? WHERE id = ?', [now, invitation['id']])
      db.execute('INSERT INTO users (organization_id, email, password_digest, role, created_at) VALUES (?, ?, ?, ?, ?)',
                 [invitation['organization_id'], invitation['email'], BCrypt::Password.create(password),
                  invitation['role'], now])
      user = db.get_first_row('SELECT * FROM users WHERE id = ?', [db.last_insert_row_id])
      session_token = issue_token(user['id'])
    end
    json_response(201, token: session_token, expires_in: TOKEN_TTL, user: user_json(user))
  end

  patch '/users/:id' do
    require_role!('admin')
    target = find_in_org!('users', params['id'])
    role = fetch_role(json_body)

    if target['role'] == 'admin' && role != 'admin'
      admins = db.get_first_value("SELECT COUNT(*) FROM users WHERE organization_id = ? AND role = 'admin'",
                                  [target['organization_id']])
      halt_json(409, 'an organisation must keep at least one admin') if admins <= 1
    end

    db.execute('UPDATE users SET role = ? WHERE id = ?', [role, target['id']])
    json_response(200, user: user_json(target.merge('role' => role)))
  end

  # --- Notes ----------------------------------------------------------------

  # Lists notes, newest first. With ?q= only notes whose title or body
  # contains the text (case-insensitive) are returned.
  get '/notes' do
    org_id = current_user['organization_id']
    limit = int_param('limit', default: 50, min: 1, max: 100)
    offset = int_param('offset', default: 0, min: 0, max: 1_000_000_000)

    sql = +'SELECT * FROM notes WHERE organization_id = ?'
    binds = [org_id]
    query = params['q']
    unless query.nil?
      halt_json(422, 'q must be a string') unless query.is_a?(String)
      halt_json(422, "q must be at most #{MAX_QUERY} characters") if query.length > MAX_QUERY
      unless query.strip.empty?
        pattern = "%#{escape_like(query.strip)}%"
        sql << " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
        binds.push(pattern, pattern)
      end
    end
    sql << ' ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?'
    binds.push(limit, offset)

    notes = db.execute(sql, binds)
    json_response(200, notes: notes.map { |n| note_json(n) }, limit: limit, offset: offset)
  end

  get '/notes/:id' do
    json_response(200, note: note_json(find_in_org!('notes', params['id'])))
  end

  post '/notes' do
    require_role!(*WRITE_ROLES)
    data = json_body
    title = fetch_title(data)
    body = fetch_string(data, 'body', max: MAX_BODY, required: false) || ''
    timestamp = now
    db.execute(<<~SQL, [current_user['organization_id'], current_user['id'], title, body, timestamp, timestamp])
      INSERT INTO notes (organization_id, author_id, title, body, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
    SQL
    note = db.get_first_row('SELECT * FROM notes WHERE id = ?', [db.last_insert_row_id])
    json_response(201, note: note_json(note))
  end

  patch '/notes/:id' do
    require_role!(*WRITE_ROLES)
    note = find_in_org!('notes', params['id'])
    data = json_body
    title = fetch_title(data, required: false)
    body = fetch_string(data, 'body', max: MAX_BODY, required: false)
    halt_json(422, 'provide title and/or body') if title.nil? && body.nil?

    db.execute('UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND organization_id = ?',
               [title || note['title'], body || note['body'], now, note['id'], note['organization_id']])
    updated = db.get_first_row('SELECT * FROM notes WHERE id = ?', [note['id']])
    json_response(200, note: note_json(updated))
  end

  delete '/notes/:id' do
    require_role!(*WRITE_ROLES)
    note = find_in_org!('notes', params['id'])
    db.execute('DELETE FROM notes WHERE id = ? AND organization_id = ?', [note['id'], note['organization_id']])
    status 204
  end
end
