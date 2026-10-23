ENV['RACK_ENV'] = 'test' # Sinatra's development host filter would reject Rack::Test's host

require 'minitest/autorun'
require 'rack/test'
require 'sqlite3'
require 'json'
require 'csv'
require_relative 'app'

class ReportAppTest < Minitest::Test
  include Rack::Test::Methods

  KEY = 'test-admin-key'.freeze

  SCHEMA = <<~SQL
    CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, name TEXT NOT NULL);
    CREATE TABLE orders (
      id INTEGER PRIMARY KEY,
      customer_id INTEGER NOT NULL REFERENCES customers(id),
      status TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE order_items (
      order_id INTEGER NOT NULL REFERENCES orders(id),
      product_id INTEGER NOT NULL REFERENCES products(id),
      quantity INTEGER NOT NULL,
      unit_price_cents INTEGER NOT NULL
    );
  SQL

  def app
    ReportApp
  end

  def setup
    db = SQLite3::Database.new(':memory:', results_as_hash: true)
    db.execute_batch(SCHEMA)
    seed(db)
    ReportApp.set :db, db
    ReportApp.set :api_keys, [KEY]
  end

  def seed(db)
    db.execute_batch(<<~SQL)
      INSERT INTO customers VALUES (1, 'Ada'), (2, 'Grace');
      INSERT INTO products VALUES (1, 'MUG-1', 'Mug'), (2, 'TEE-1', 'Tee'), (3, 'HAT-1', 'Hat');
      INSERT INTO orders VALUES
        (1, 1, 'paid',      '2026-01-05 10:00:00'),
        (2, 2, 'shipped',   '2026-01-06 09:00:00'),
        (3, 1, 'cancelled', '2026-01-06 12:00:00'),
        (4, 2, 'delivered', '2026-02-10 08:00:00');
      INSERT INTO order_items VALUES
        (1, 1, 2, 500),
        (1, 2, 1, 2000),
        (2, 1, 1, 500),
        (3, 3, 5, 1000),
        (4, 2, 2, 2000),
        (4, 3, 1, 1000);
    SQL
  end

  def admin_get(path, query = {}, key: KEY)
    get path, query, { 'HTTP_X_API_KEY' => key }
    last_response
  end

  def json_body
    JSON.parse(last_response.body)
  end

  # Authentication

  def test_rejects_missing_key
    get '/reports/sales', { from: '2026-01-01', to: '2026-12-31' }
    assert_equal 401, last_response.status
  end

  def test_rejects_wrong_key
    res = admin_get('/reports/sales', { from: '2026-01-01', to: '2026-12-31' }, key: 'nope')
    assert_equal 401, res.status
  end

  # Sales report

  def test_sales_grouped_by_day
    res = admin_get('/reports/sales', { from: '2026-01-01', to: '2026-02-28', group_by: 'day' })

    assert_equal 200, res.status
    assert_equal [
      ['2026-01-05', 1, 3000],
      ['2026-01-06', 1, 500],
      ['2026-02-10', 1, 5000]
    ], json_body['periods'].map { |p| [p['period'], p['order_count'], p['revenue_cents']] }
  end

  def test_sales_grouped_by_month
    res = admin_get('/reports/sales', { from: '2026-01-01', to: '2026-02-28', group_by: 'month' })

    assert_equal [
      ['2026-01', 2, 3500],
      ['2026-02', 1, 5000]
    ], json_body['periods'].map { |p| [p['period'], p['order_count'], p['revenue_cents']] }
  end

  def test_sales_grouped_by_week
    res = admin_get('/reports/sales', { from: '2026-01-01', to: '2026-02-28', group_by: 'week' })

    assert_equal [
      ['2026-W01', 2, 3500],
      ['2026-W06', 1, 5000]
    ], json_body['periods'].map { |p| [p['period'], p['order_count'], p['revenue_cents']] }
  end

  def test_sales_defaults_to_day_grouping
    admin_get('/reports/sales', { from: '2026-01-01', to: '2026-02-28' })
    assert_equal 'day', json_body['group_by']
  end

  def test_sales_rejects_unknown_grouping
    res = admin_get('/reports/sales', { from: '2026-01-01', to: '2026-02-28', group_by: 'quarter' })
    assert_equal 400, res.status
  end

  def test_sales_rejects_reversed_range
    res = admin_get('/reports/sales', { from: '2026-02-28', to: '2026-01-01' })
    assert_equal 400, res.status
  end

  def test_sales_rejects_malformed_date
    res = admin_get('/reports/sales', { from: '2026-13-01', to: '2026-02-28' })
    assert_equal 400, res.status
  end

  # Top products

  def test_top_products_by_revenue
    res = admin_get('/reports/top-products', { from: '2026-01-01', to: '2026-02-28' })

    assert_equal 200, res.status
    products = json_body['products']
    assert_equal %w[Tee Mug Hat], products.map { |p| p['name'] }
    assert_equal [6000, 1500, 1000], products.map { |p| p['revenue_cents'] }
  end

  def test_top_products_respects_limit
    res = admin_get('/reports/top-products', { from: '2026-01-01', to: '2026-02-28', limit: 1 })

    assert_equal ['Tee'], json_body['products'].map { |p| p['name'] }
  end

  def test_top_products_rejects_out_of_range_limit
    res = admin_get('/reports/top-products', { from: '2026-01-01', to: '2026-02-28', limit: 0 })
    assert_equal 400, res.status
  end

  # Order export

  def export_ids(query)
    res = admin_get('/orders/export', query)
    assert_equal 200, res.status
    json_body.map { |o| o['id'] }
  end

  def test_export_defaults_to_created_at_ascending
    assert_equal [1, 2, 3, 4], export_ids({})
  end

  def test_export_sorts_by_chosen_column
    assert_equal [3, 4, 1, 2], export_ids({ sort: 'total_cents', direction: 'desc' })
  end

  def test_export_filters_by_status
    assert_equal [2], export_ids({ status: 'shipped' })
  end

  def test_export_filters_by_customer
    assert_equal [1, 3], export_ids({ customer_id: 1 })
  end

  def test_export_filters_by_date_range
    assert_equal [4], export_ids({ from: '2026-02-01' })
    assert_equal [1, 2, 3], export_ids({ to: '2026-01-31' })
  end

  def test_export_csv
    res = admin_get('/orders/export', { format: 'csv', sort: 'id', direction: 'asc' })

    assert_equal 200, res.status
    assert_includes res.content_type, 'text/csv'
    table = CSV.parse(res.body, headers: true)
    assert_equal %w[id created_at status customer_id customer_name total_cents], table.headers
    assert_equal %w[1 2 3 4], table['id']
    assert_equal %w[Ada Grace Ada Grace], table['customer_name']
  end

  def test_export_rejects_unknown_sort_column
    # Injection attempts must never reach the SQL text.
    res = admin_get('/orders/export', { sort: 'id; DROP TABLE orders' })
    assert_equal 400, res.status
  end

  def test_export_rejects_bad_direction
    res = admin_get('/orders/export', { direction: 'sideways' })
    assert_equal 400, res.status
  end

  def test_export_rejects_unknown_format
    res = admin_get('/orders/export', { format: 'xml' })
    assert_equal 400, res.status
  end

  def test_export_rejects_non_integer_customer
    res = admin_get('/orders/export', { customer_id: 'abc' })
    assert_equal 400, res.status
  end
end
