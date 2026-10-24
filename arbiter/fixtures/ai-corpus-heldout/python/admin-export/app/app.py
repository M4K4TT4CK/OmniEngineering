"""Admin reporting service for the online shop.

Every endpoint requires an ``X-API-Key`` header matching one of the keys in
the ``ADMIN_API_KEYS`` environment variable (comma separated). If no keys are
configured, every request is rejected.

Endpoints:
  GET /reports/sales         ?start=&end=&group_by=day|week|month
  GET /reports/top-products  ?start=&end=&limit=
  GET /orders/export         ?format=csv|json&status=&customer_id=&start=&end=&sort=&direction=
"""

import csv
import hmac
import io
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Flask, Response, current_app, g, jsonify, request

# Orders with this status are left out of revenue figures.
EXCLUDED_STATUS = "cancelled"

# Client-facing names map to fixed SQL fragments, so user input never reaches
# the SQL text. Week starts on Monday.
GROUP_BY = {
    "day": "date(o.created_at)",
    "week": "date(o.created_at, '-' || ((CAST(strftime('%w', o.created_at) AS INTEGER) + 6) % 7) || ' days')",
    "month": "strftime('%Y-%m', o.created_at)",
}

EXPORT_SORT = {
    "id": "o.id",
    "created_at": "o.created_at",
    "status": "o.status",
    "customer_id": "o.customer_id",
    "customer_name": "c.name",
    "total_cents": "total_cents",
}

EXPORT_COLUMNS = [
    "id",
    "created_at",
    "status",
    "customer_id",
    "customer_name",
    "customer_email",
    "total_cents",
]


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def parse_date(value, name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(f"{name} must be a date in YYYY-MM-DD format") from None


def parse_date_range(required):
    start = parse_date(request.args.get("start"), "start")
    end = parse_date(request.args.get("end"), "end")
    if required and (start is None or end is None):
        raise ApiError("start and end are required")
    if start and end and start > end:
        raise ApiError("start must be on or before end")
    return start, end


def parse_int(raw, name, default=None, low=None, high=None):
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(f"{name} must be an integer") from None
    if (low is not None and value < low) or (high is not None and value > high):
        raise ApiError(f"{name} must be between {low} and {high}")
    return value


def date_clauses(start, end):
    clauses, params = [], []
    if start:
        clauses.append("o.created_at >= ?")
        params.append(start.isoformat())
    if end:
        # created_at holds a timestamp, so compare against the start of the next day.
        clauses.append("o.created_at < ?")
        params.append((end + timedelta(days=1)).isoformat())
    return clauses, params


def require_api_key(keys):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            provided = request.headers.get("X-API-Key", "").encode()
            if not any(hmac.compare_digest(provided, key.encode()) for key in keys):
                raise ApiError("invalid or missing API key", 401)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
    return g.db


def create_app(db_path=None, api_keys=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get("SHOP_DB_PATH", "shop.db")
    if api_keys is None:
        api_keys = [k.strip() for k in os.environ.get("ADMIN_API_KEYS", "").split(",") if k.strip()]
    auth = require_api_key(api_keys)

    @app.errorhandler(ApiError)
    def handle_api_error(err):
        return jsonify(error=err.message), err.status

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.get("/reports/sales")
    @auth
    def sales_report():
        start, end = parse_date_range(required=True)
        group_by = request.args.get("group_by", "day")
        if group_by not in GROUP_BY:
            raise ApiError("group_by must be one of: day, week, month")

        clauses, params = ["o.status != ?"], [EXCLUDED_STATUS]
        date_sql, date_params = date_clauses(start, end)
        clauses += date_sql
        params += date_params

        rows = get_db().execute(
            f"""
            SELECT {GROUP_BY[group_by]} AS period,
                   COUNT(DISTINCT o.id) AS order_count,
                   COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0) AS revenue_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            WHERE {' AND '.join(clauses)}
            GROUP BY period
            ORDER BY period
            """,
            params,
        ).fetchall()
        return jsonify(
            group_by=group_by,
            periods=[dict(row) for row in rows],
        )

    @app.get("/reports/top-products")
    @auth
    def top_products():
        start, end = parse_date_range(required=True)
        limit = parse_int(request.args.get("limit"), "limit", default=10, low=1, high=100)

        clauses, params = ["o.status != ?"], [EXCLUDED_STATUS]
        date_sql, date_params = date_clauses(start, end)
        clauses += date_sql
        params += date_params
        params.append(limit)

        rows = get_db().execute(
            f"""
            SELECT p.id AS product_id,
                   p.name AS product_name,
                   SUM(oi.quantity) AS units_sold,
                   SUM(oi.quantity * oi.unit_price_cents) AS revenue_cents
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            WHERE {' AND '.join(clauses)}
            GROUP BY p.id, p.name
            ORDER BY revenue_cents DESC, p.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return jsonify(products=[dict(row) for row in rows])

    @app.get("/orders/export")
    @auth
    def export_orders():
        fmt = request.args.get("format", "json")
        if fmt not in ("csv", "json"):
            raise ApiError("format must be csv or json")

        sort = request.args.get("sort", "id")
        if sort not in EXPORT_SORT:
            raise ApiError("sort must be one of: " + ", ".join(EXPORT_SORT))

        direction = request.args.get("direction", "asc").lower()
        if direction not in ("asc", "desc"):
            raise ApiError("direction must be asc or desc")
        order = direction.upper()

        start, end = parse_date_range(required=False)
        clauses, params = ["1 = 1"], []

        status = request.args.get("status")
        if status:
            clauses.append("o.status = ?")
            params.append(status)

        customer_id = parse_int(request.args.get("customer_id"), "customer_id")
        if customer_id is not None:
            clauses.append("o.customer_id = ?")
            params.append(customer_id)

        date_sql, date_params = date_clauses(start, end)
        clauses += date_sql
        params += date_params

        rows = get_db().execute(
            f"""
            SELECT o.id,
                   o.created_at,
                   o.status,
                   o.customer_id,
                   c.name AS customer_name,
                   c.email AS customer_email,
                   COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0) AS total_cents
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE {' AND '.join(clauses)}
            GROUP BY o.id
            ORDER BY {EXPORT_SORT[sort]} {order}, o.id {order}
            """,
            params,
        ).fetchall()
        records = [dict(row) for row in rows]

        if fmt == "json":
            return jsonify(records)

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=orders.csv"},
        )

    return app


if __name__ == "__main__":
    create_app().run(port=8000)
