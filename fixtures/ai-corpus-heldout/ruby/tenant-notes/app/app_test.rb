ENV['RACK_ENV'] = 'test'
ENV['DATABASE_PATH'] = ':memory:'

require 'bcrypt'
BCrypt::Engine.cost = BCrypt::Engine::MIN_COST

require 'cgi'
require 'minitest/autorun'
require 'rack/test'
require_relative 'app'

class NotesAppTest < Minitest::Test
  include Rack::Test::Methods

  PASSWORD = 'correct horse battery'.freeze

  def app
    NotesApp
  end

  def setup
    NotesApp.connect(':memory:')
    @admin = signup('Acme', 'admin@acme.test')
    @editor = invite_and_accept(@admin, 'editor@acme.test', 'editor')
    @viewer = invite_and_accept(@admin, 'viewer@acme.test', 'viewer')
  end

  # --- helpers ---------------------------------------------------------------

  def request_json(method, path, body: nil, token: nil)
    env = { 'CONTENT_TYPE' => 'application/json' }
    env['HTTP_AUTHORIZATION'] = "Bearer #{token}" if token
    send(method, path, body.nil? ? {} : JSON.generate(body), env)
    last_response
  end

  def json
    JSON.parse(last_response.body)
  end

  def signup(org, email)
    request_json(:post, '/signup', body: { organization: org, email: email, password: PASSWORD })
    assert_equal 201, last_response.status, last_response.body
    json['token']
  end

  def invite_and_accept(admin_token, email, role)
    request_json(:post, '/invitations', token: admin_token, body: { email: email, role: role })
    assert_equal 201, last_response.status, last_response.body
    request_json(:post, '/invitations/accept', body: { token: json['token'], password: PASSWORD })
    assert_equal 201, last_response.status, last_response.body
    json['token']
  end

  def create_note(token, title, body = '')
    request_json(:post, '/notes', token: token, body: { title: title, body: body })
    assert_equal 201, last_response.status, last_response.body
    json['note']
  end

  def user_id(token)
    request_json(:get, '/me', token: token)
    json['user']['id']
  end

  # --- authentication --------------------------------------------------------

  def test_login_returns_working_token
    request_json(:post, '/login', body: { email: 'EDITOR@acme.test', password: PASSWORD })
    assert_equal 200, last_response.status
    token = json['token']
    request_json(:get, '/me', token: token)
    assert_equal 'editor@acme.test', json['user']['email']
    assert_equal 'editor', json['user']['role']
  end

  def test_login_rejects_bad_credentials
    request_json(:post, '/login', body: { email: 'editor@acme.test', password: 'wrong password' })
    assert_equal 401, last_response.status
    request_json(:post, '/login', body: { email: 'nobody@acme.test', password: PASSWORD })
    assert_equal 401, last_response.status
    request_json(:post, '/login', body: { email: ['x'], password: { a: 1 } })
    assert_equal 401, last_response.status
  end

  def test_requires_valid_token
    request_json(:get, '/notes')
    assert_equal 401, last_response.status
    request_json(:get, '/notes', token: 'not-a-real-token')
    assert_equal 401, last_response.status
  end

  def test_logout_revokes_token
    request_json(:delete, '/session', token: @editor)
    assert_equal 204, last_response.status
    request_json(:get, '/notes', token: @editor)
    assert_equal 401, last_response.status
  end

  def test_expired_token_is_rejected
    NotesApp.settings.db.execute('UPDATE auth_tokens SET expires_at = ?', [Time.now.to_i - 1])
    request_json(:get, '/notes', token: @editor)
    assert_equal 401, last_response.status
  end

  def test_signup_rejects_duplicate_email_and_weak_password
    request_json(:post, '/signup', body: { organization: 'Other', email: 'admin@acme.test', password: PASSWORD })
    assert_equal 409, last_response.status
    request_json(:post, '/signup', body: { organization: 'Other', email: 'new@other.test', password: 'short' })
    assert_equal 422, last_response.status
  end

  def test_malformed_json_is_rejected
    post '/login', '{not json', 'CONTENT_TYPE' => 'application/json'
    assert_equal 400, last_response.status
  end

  # --- notes and roles -------------------------------------------------------

  def test_editor_can_create_update_and_delete
    note = create_note(@editor, 'Plan', 'first draft')
    assert_equal user_id(@editor), note['author_id']

    request_json(:patch, "/notes/#{note['id']}", token: @editor, body: { body: 'second draft' })
    assert_equal 200, last_response.status
    assert_equal 'Plan', json['note']['title']
    assert_equal 'second draft', json['note']['body']

    request_json(:delete, "/notes/#{note['id']}", token: @editor)
    assert_equal 204, last_response.status
    request_json(:get, "/notes/#{note['id']}", token: @editor)
    assert_equal 404, last_response.status
  end

  def test_admin_can_write_notes
    note = create_note(@admin, 'Admin note')
    request_json(:delete, "/notes/#{note['id']}", token: @admin)
    assert_equal 204, last_response.status
  end

  def test_viewer_can_read_but_not_write
    note = create_note(@editor, 'Visible', 'hello')

    request_json(:get, '/notes', token: @viewer)
    assert_equal 200, last_response.status
    assert_equal ['Visible'], json['notes'].map { |n| n['title'] }
    request_json(:get, "/notes/#{note['id']}", token: @viewer)
    assert_equal 'hello', json['note']['body']

    request_json(:post, '/notes', token: @viewer, body: { title: 'Nope' })
    assert_equal 403, last_response.status
    request_json(:patch, "/notes/#{note['id']}", token: @viewer, body: { title: 'Nope' })
    assert_equal 403, last_response.status
    request_json(:delete, "/notes/#{note['id']}", token: @viewer)
    assert_equal 403, last_response.status
  end

  def test_note_validation
    request_json(:post, '/notes', token: @editor, body: { body: 'no title' })
    assert_equal 422, last_response.status
    request_json(:post, '/notes', token: @editor, body: { title: '   ' })
    assert_equal 422, last_response.status
    request_json(:post, '/notes', token: @editor, body: { title: 'x' * 201 })
    assert_equal 422, last_response.status
    request_json(:post, '/notes', token: @editor, body: { title: 'ok', body: 42 })
    assert_equal 422, last_response.status

    note = create_note(@editor, 'ok')
    request_json(:patch, "/notes/#{note['id']}", token: @editor, body: {})
    assert_equal 422, last_response.status
  end

  def test_search_matches_title_or_body_case_insensitively
    create_note(@editor, 'Groceries', 'milk and eggs')
    create_note(@editor, 'Work', 'buy MILK for the office')
    create_note(@editor, 'Holiday', 'book flights')

    request_json(:get, '/notes?q=milk', token: @viewer)
    assert_equal %w[Work Groceries], json['notes'].map { |n| n['title'] }
    request_json(:get, '/notes?q=holiday', token: @viewer)
    assert_equal ['Holiday'], json['notes'].map { |n| n['title'] }
  end

  def test_search_treats_wildcards_literally
    create_note(@editor, '100% done')
    create_note(@editor, '100 percent')
    request_json(:get, "/notes?q=#{CGI.escape('100%')}", token: @editor)
    assert_equal ['100% done'], json['notes'].map { |n| n['title'] }
    request_json(:get, '/notes?q=_', token: @editor)
    assert_empty json['notes']
  end

  def test_list_pagination
    3.times { |i| create_note(@editor, "n#{i}") }
    request_json(:get, '/notes?limit=2', token: @editor)
    assert_equal 2, json['notes'].length
    request_json(:get, '/notes?limit=2&offset=2', token: @editor)
    assert_equal 1, json['notes'].length
    request_json(:get, '/notes?limit=abc', token: @editor)
    assert_equal 422, last_response.status
  end

  # --- tenant isolation ------------------------------------------------------

  def test_organisations_cannot_see_each_others_notes
    note = create_note(@editor, 'Acme secret', 'classified')
    other = signup('Globex', 'admin@globex.test')

    request_json(:get, '/notes', token: other)
    assert_empty json['notes']
    request_json(:get, '/notes?q=classified', token: other)
    assert_empty json['notes']
    request_json(:get, "/notes/#{note['id']}", token: other)
    assert_equal 404, last_response.status
    request_json(:patch, "/notes/#{note['id']}", token: other, body: { title: 'pwned' })
    assert_equal 404, last_response.status
    request_json(:delete, "/notes/#{note['id']}", token: other)
    assert_equal 404, last_response.status

    request_json(:get, "/notes/#{note['id']}", token: @editor)
    assert_equal 'Acme secret', json['note']['title']
  end

  def test_admin_cannot_manage_users_in_other_organisations
    other = signup('Globex', 'admin@globex.test')
    request_json(:patch, "/users/#{user_id(@viewer)}", token: other, body: { role: 'admin' })
    assert_equal 404, last_response.status
    request_json(:get, '/users', token: other)
    assert_equal ['admin@globex.test'], json['users'].map { |u| u['email'] }
  end

  # --- invitations and roles -------------------------------------------------

  def test_only_admins_can_invite
    request_json(:post, '/invitations', token: @editor, body: { email: 'x@acme.test', role: 'viewer' })
    assert_equal 403, last_response.status
    request_json(:post, '/invitations', token: @viewer, body: { email: 'x@acme.test', role: 'viewer' })
    assert_equal 403, last_response.status
  end

  def test_invitation_validation
    request_json(:post, '/invitations', token: @admin, body: { email: 'x@acme.test', role: 'owner' })
    assert_equal 422, last_response.status
    request_json(:post, '/invitations', token: @admin, body: { email: 'editor@acme.test', role: 'viewer' })
    assert_equal 409, last_response.status
  end

  def test_invitation_token_is_single_use
    request_json(:post, '/invitations', token: @admin, body: { email: 'new@acme.test', role: 'viewer' })
    invite = json['token']
    request_json(:post, '/invitations/accept', body: { token: invite, password: PASSWORD })
    assert_equal 201, last_response.status
    assert_equal 'viewer', json['user']['role']
    request_json(:post, '/invitations/accept', body: { token: invite, password: PASSWORD })
    assert_equal 404, last_response.status
  end

  def test_expired_invitation_is_rejected
    request_json(:post, '/invitations', token: @admin, body: { email: 'late@acme.test', role: 'viewer' })
    invite = json['token']
    NotesApp.settings.db.execute('UPDATE invitations SET expires_at = ?', [Time.now.to_i - 1])
    request_json(:post, '/invitations/accept', body: { token: invite, password: PASSWORD })
    assert_equal 404, last_response.status
  end

  def test_admin_can_change_roles_and_it_applies_immediately
    viewer_id = user_id(@viewer)
    request_json(:patch, "/users/#{viewer_id}", token: @admin, body: { role: 'editor' })
    assert_equal 200, last_response.status
    assert_equal 'editor', json['user']['role']
    create_note(@viewer, 'Now allowed')

    request_json(:patch, "/users/#{user_id(@editor)}", token: @admin, body: { role: 'viewer' })
    request_json(:post, '/notes', token: @editor, body: { title: 'Not any more' })
    assert_equal 403, last_response.status
  end

  def test_non_admins_cannot_change_roles
    request_json(:patch, "/users/#{user_id(@editor)}", token: @editor, body: { role: 'admin' })
    assert_equal 403, last_response.status
    request_json(:get, '/users', token: @editor)
    assert_equal 403, last_response.status
  end

  def test_last_admin_cannot_be_demoted
    admin_id = user_id(@admin)
    request_json(:patch, "/users/#{admin_id}", token: @admin, body: { role: 'editor' })
    assert_equal 409, last_response.status

    request_json(:patch, "/users/#{user_id(@editor)}", token: @admin, body: { role: 'admin' })
    request_json(:patch, "/users/#{admin_id}", token: @admin, body: { role: 'editor' })
    assert_equal 200, last_response.status
  end

  def test_invalid_role_is_rejected
    request_json(:patch, "/users/#{user_id(@viewer)}", token: @admin, body: { role: 'superuser' })
    assert_equal 422, last_response.status
  end
end
