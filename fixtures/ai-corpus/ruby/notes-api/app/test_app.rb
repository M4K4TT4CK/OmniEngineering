ENV['APP_ENV'] = 'test'
ENV['RACK_ENV'] = 'test'

require 'minitest/autorun'
require 'rack/test'
require_relative 'app'

class NotesAppTest < Minitest::Test
  include Rack::Test::Methods

  def app = NotesApp

  def setup
    NotesApp.set :db, Database.new(':memory:')
    @time = Time.at(1_700_000_000 - (1_700_000_000 % 60)) # start of a rate-limit window
    NotesApp.set :clock, -> { @time }
    header 'Content-Type', 'application/json'
  end

  def post_json(path, payload, token: nil)
    header 'Authorization', token ? "Bearer #{token}" : nil
    post path, JSON.generate(payload)
  end

  def put_json(path, payload, token: nil)
    header 'Authorization', token ? "Bearer #{token}" : nil
    put path, JSON.generate(payload)
  end

  def authed(verb, path, token)
    header 'Authorization', "Bearer #{token}"
    send(verb, path)
  end

  def body = JSON.parse(last_response.body)

  def signup_and_login(name = 'alice', password = 'password123')
    post_json '/signup', { username: name, password: password }
    post_json '/login', { username: name, password: password }
    body['token']
  end

  def create_note(token, title = 'T', text = '')
    post_json '/notes', { title: title, body: text }, token: token
    body
  end

  # --- accounts ---

  def test_signup_and_login
    post_json '/signup', { username: 'alice', password: 'password123' }
    assert_equal 201, last_response.status
    assert_equal 'alice', body['username']

    post_json '/login', { username: 'alice', password: 'password123' }
    assert_equal 200, last_response.status
    refute_empty body['token']
  end

  def test_signup_validation
    post_json '/signup', { username: 'a', password: 'password123' }
    assert_equal 422, last_response.status
    post_json '/signup', { username: 'alice', password: 'short' }
    assert_equal 422, last_response.status
    post_json '/signup', { username: 'alice' }
    assert_equal 422, last_response.status
  end

  def test_duplicate_username_case_insensitive
    signup_and_login('alice')
    post_json '/signup', { username: 'ALICE', password: 'password123' }
    assert_equal 409, last_response.status
  end

  def test_login_failures
    signup_and_login
    post_json '/login', { username: 'alice', password: 'wrongpassword' }
    assert_equal 401, last_response.status
    post_json '/login', { username: 'nobody', password: 'password123' }
    assert_equal 401, last_response.status
  end

  def test_invalid_json
    header 'Authorization', nil
    post '/signup', 'not json'
    assert_equal 400, last_response.status
    post '/signup', '[1]'
    assert_equal 400, last_response.status
  end

  def test_token_required_and_expires
    header 'Authorization', nil
    get '/notes'
    assert_equal 401, last_response.status
    authed :get, '/notes', 'bogus'
    assert_equal 401, last_response.status

    token = signup_and_login
    authed :get, '/notes', token
    assert_equal 200, last_response.status

    @time += NotesApp::TOKEN_TTL + 1
    authed :get, '/notes', token
    assert_equal 401, last_response.status
  end

  def test_token_not_stored_in_plaintext
    token = signup_and_login
    stored = NotesApp.database.execute('SELECT token_hash FROM tokens').map { |r| r['token_hash'] }
    refute_includes stored, token
  end

  # --- notes ---

  def test_note_crud
    token = signup_and_login
    note = create_note(token, 'Groceries', 'milk')
    assert_equal 201, last_response.status
    assert_equal 'Groceries', note['title']

    authed :get, "/notes/#{note['id']}", token
    assert_equal 'milk', body['body']

    put_json "/notes/#{note['id']}", { body: 'milk, eggs' }, token: token
    assert_equal 200, last_response.status
    assert_equal 'milk, eggs', body['body']
    assert_equal 'Groceries', body['title']

    authed :get, '/notes', token
    assert_equal 1, body['notes'].size

    authed :delete, "/notes/#{note['id']}", token
    assert_equal 204, last_response.status
    authed :get, "/notes/#{note['id']}", token
    assert_equal 404, last_response.status
  end

  def test_note_validation
    token = signup_and_login
    post_json '/notes', { body: 'no title' }, token: token
    assert_equal 422, last_response.status
    post_json '/notes', { title: 'x' * 201 }, token: token
    assert_equal 422, last_response.status
    note = create_note(token)
    put_json "/notes/#{note['id']}", {}, token: token
    assert_equal 422, last_response.status
  end

  def test_users_cannot_access_each_others_notes
    alice = signup_and_login('alice')
    bob = signup_and_login('bobby', 'password456')
    note = create_note(alice, 'secret')

    authed :get, "/notes/#{note['id']}", bob
    assert_equal 404, last_response.status
    put_json "/notes/#{note['id']}", { title: 'hacked' }, token: bob
    assert_equal 404, last_response.status
    authed :delete, "/notes/#{note['id']}", bob
    assert_equal 404, last_response.status
    authed :get, '/notes', bob
    assert_empty body['notes']

    authed :get, "/notes/#{note['id']}", alice
    assert_equal 'secret', body['title']
  end

  def test_search
    token = signup_and_login
    create_note(token, 'Shopping list', 'apples')
    create_note(token, 'Ideas', 'build a 100% fun app')
    create_note(token, 'Other', 'nothing')

    authed :get, '/notes?q=apples', token
    assert_equal ['Shopping list'], body['notes'].map { |n| n['title'] }
    authed :get, '/notes?q=ideas', token
    assert_equal ['Ideas'], body['notes'].map { |n| n['title'] }
    # LIKE wildcards are treated literally
    authed :get, '/notes?q=%25', token
    assert_equal ['Ideas'], body['notes'].map { |n| n['title'] }
    authed :get, '/notes?q=_', token
    assert_empty body['notes']
  end

  def test_search_only_covers_own_notes
    alice = signup_and_login('alice')
    bob = signup_and_login('bobby', 'password456')
    create_note(alice, 'findme')
    authed :get, '/notes?q=findme', bob
    assert_empty body['notes']
  end

  def test_pagination
    token = signup_and_login
    5.times { |i| create_note(token, "n#{i}") }
    authed :get, '/notes?limit=2&offset=1', token
    assert_equal 2, body['notes'].size
  end

  # --- rate limiting ---

  def test_rate_limit_per_user
    alice = signup_and_login('alice')
    bob = signup_and_login('bobby', 'password456')

    # Signup/login were anonymous (per IP), so alice's own budget is untouched.
    NotesApp::RATE_LIMIT.times do
      authed :get, '/notes', alice
      assert_equal 200, last_response.status
    end
    authed :get, '/notes', alice
    assert_equal 429, last_response.status
    assert last_response.headers['Retry-After']

    # Other users are unaffected
    authed :get, '/notes', bob
    assert_equal 200, last_response.status

    # New window resets the limit
    @time += 60
    authed :get, '/notes', alice
    assert_equal 200, last_response.status
  end

  def test_rate_limit_anonymous_by_ip
    NotesApp::RATE_LIMIT.times do
      post_json '/login', { username: 'x', password: 'y' }
      assert_equal 401, last_response.status
    end
    post_json '/login', { username: 'x', password: 'y' }
    assert_equal 429, last_response.status
  end
end
