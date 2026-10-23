require 'sinatra/base'
require 'sqlite3'
require 'json'
require 'csv'
require 'date'
require 'openssl'

# Admin reporting API over the shop's SQLite database.
#
# Revenue is sum(quantity * unit_price_cents) over order_items. Cancelled
# orders are left out of revenue reports; the export lists every order.
# Dates are matched on the calendar day of orders.created_at (ISO 8601 text).
class ReportApp < Sinatra::Base
  DEFAULT_LIMIT = 10
  MAX_LIMIT = 100

  # strftime patterns for each supported grouping.
  GROUPINGS = {
    'day' => '%Y-%m-%d',
    'week' => '%Y-W%W',
    'month' => '%Y-%m'
  }.freeze

  # Sort keys callers may choose, mapped to SQL. Only these reach the query text.
  EXPORT_COLUMNS = {
    'id' => 'o.id',
    'created_at' => 'o.created_at',
    'status' => 'o.status',
    'customer_id' => 'o.customer_id',
    'customer_name' => 'c.name',
    'total_cents' => 'total_cents'
  }.freeze

  set :show_exceptions, false

  before do
    authenticate!
  end

  get '/reports/sales' do
    from, to = report_range
    group_by = params.fetch('group_by', 'day')
    pattern = GROUPINGS.fetch(group_by) do
      bad_request!("group_by must be one of: #{GROUPINGS.keys.join(', ')}")
    end

    periods = settings.db.execute(<<~SQL, [pattern, from, to])
      SELECT strftime(?, o.created_at) AS period,
             COUNT(DISTINCT o.id) AS order_count,
             SUM(oi.quantity * oi.unit_price_cents) AS revenue_cents
      FROM orders o
      JOIN order_items oi ON oi.order_id = o.id
      WHERE date(o.created_at) BETWEEN ? AND ?
        AND o.status <> 'cancelled'
      GROUP BY period
      ORDER BY period
    SQL

    send_json(group_by: group_by, from: from, to: to, periods: periods)
  end

  get '/reports/top-products' do
    from, to = report_range
    limit = parse_limit

    products = settings.db.execute(<<~SQL, [from, to, limit])
      SELECT p.id AS product_id, p.sku, p.name,
             SUM(oi.quantity) AS units_sold,
             SUM(oi.quantity * oi.unit_price_cents) AS revenue_cents
      FROM order_items oi
      JOIN orders o ON o.id = oi.order_id
      JOIN products p ON p.id = oi.product_id
      WHERE date(o.created_at) BETWEEN ? AND ?
        AND o.status <> 'cancelled'
      GROUP BY p.id
      ORDER BY revenue_cents DESC, p.id ASC
      LIMIT ?
    SQL

    send_json(from: from, to: to, limit: limit, products: products)
  end

  get '/orders/export' do
    format = params.fetch('format', 'json')
    bad_request!('format must be csv or json') unless %w[csv json].include?(format)

    sort_column = EXPORT_COLUMNS.fetch(params.fetch('sort', 'created_at')) do
      bad_request!("sort must be one of: #{EXPORT_COLUMNS.keys.join(', ')}")
    end
    direction = params.fetch('direction', 'asc').downcase
    bad_request!('direction must be asc or desc') unless %w[asc desc].include?(direction)

    filters, args = export_filters
    where_sql = filters.empty? ? '' : "WHERE #{filters.join(' AND ')}"

    rows = settings.db.execute(<<~SQL, args)
      SELECT o.id, o.created_at, o.status, o.customer_id, c.name AS customer_name,
             (SELECT COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0)
                FROM order_items oi
               WHERE oi.order_id = o.id) AS total_cents
      FROM orders o
      JOIN customers c ON c.id = o.customer_id
      #{where_sql}
      ORDER BY #{sort_column} #{direction}, o.id ASC
    SQL

    if format == 'csv'
      content_type 'text/csv'
      CSV.generate do |csv|
        csv << EXPORT_COLUMNS.keys
        rows.each { |row| csv << row.values_at(*EXPORT_COLUMNS.keys) }
      end
    else
      send_json(rows)
    end
  end

  helpers do
    def authenticate!
      presented = request.env['HTTP_X_API_KEY'].to_s
      valid = settings.api_keys.any? { |key| same_secret?(key, presented) }
      halt 401, send_json(error: 'invalid or missing API key') unless valid
    end

    def same_secret?(expected, given)
      return false if given.empty? || expected.bytesize != given.bytesize
      OpenSSL.fixed_length_secure_compare(expected, given)
    end

    def send_json(data)
      content_type :json
      JSON.generate(data)
    end

    def bad_request!(message)
      halt 400, send_json(error: message)
    end

    def present?(value)
      !value.nil? && !value.empty?
    end

    # Parses a YYYY-MM-DD parameter and returns its canonical form.
    def parse_date(name)
      Date.iso8601(params[name].to_s).iso8601
    rescue Date::Error
      bad_request!("#{name} must be a date in YYYY-MM-DD form")
    end

    def optional_date(name)
      parse_date(name) if present?(params[name])
    end

    def report_range
      from = parse_date('from')
      to = parse_date('to')
      bad_request!('from must not be after to') if from > to
      [from, to]
    end

    def parse_limit
      limit = Integer(params.fetch('limit', DEFAULT_LIMIT.to_s), exception: false)
      bad_request!("limit must be an integer from 1 to #{MAX_LIMIT}") unless limit&.between?(1, MAX_LIMIT)
      limit
    end

    def export_filters
      filters = []
      args = []

      if present?(params['status'])
        filters << 'o.status = ?'
        args << params['status']
      end

      if present?(params['customer_id'])
        customer_id = Integer(params['customer_id'], exception: false)
        bad_request!('customer_id must be an integer') if customer_id.nil?
        filters << 'o.customer_id = ?'
        args << customer_id
      end

      from = optional_date('from')
      to = optional_date('to')
      bad_request!('from must not be after to') if from && to && from > to

      if from
        filters << 'date(o.created_at) >= ?'
        args << from
      end

      if to
        filters << 'date(o.created_at) <= ?'
        args << to
      end

      [filters, args]
    end
  end
end
