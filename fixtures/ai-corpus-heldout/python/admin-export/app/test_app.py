import csv
import io
import sqlite3

import pytest

from app import create_app

KEY = "test-key"

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);
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
"""

SEED = """
INSERT INTO customers VALUES (1, 'Alice', 'alice@example.com');
INSERT INTO customers VALUES (2, 'Bob', 'bob@example.com');

INSERT INTO products VALUES (1, 'Widget', 500);
INSERT INTO products VALUES (2, 'Gadget', 1000);

INSERT INTO orders VALUES (1, 1, 'paid',      '2024-01-01 10:00:00');
INSERT INTO orders VALUES (2, 2, 'shipped',   '2024-01-02 09:00:00');
INSERT INTO orders VALUES (3, 1, 'cancelled', '2024-01-08 12:00:00');
INSERT INTO orders VALUES (4, 2, 'paid',      '2024-02-03 08:00:00');
INSERT INTO orders VALUES (5, 1, 'paid',      '2024-01-07 18:30:00');

INSERT INTO order_items VALUES (1, 1, 2, 500);
INSERT INTO order_items VALUES (1, 2, 1, 1000);
INSERT INTO order_items VALUES (2, 1, 1, 500);
INSERT INTO order_items VALUES (3, 2, 3, 1000);
INSERT INTO order_items VALUES (4, 2, 1, 1000);
INSERT INTO order_items VALUES (5, 1, 2, 500);
"""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executescript(SEED)
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def client(db_path):
    app = create_app(db_path=db_path, api_keys=[KEY])
    app.config["TESTING"] = True
    return app.test_client()


def get(client, path, key=KEY):
    headers = {"X-API-Key": key} if key else {}
    return client.get(path, headers=headers)


# Authentication

def test_missing_key_is_rejected(client):
    assert get(client, "/reports/top-products?start=2024-01-01&end=2024-01-31", key=None).status_code == 401


def test_wrong_key_is_rejected(client):
    assert get(client, "/orders/export", key="nope").status_code == 401


def test_no_configured_keys_fails_closed(db_path):
    app = create_app(db_path=db_path, api_keys=[])
    app.config["TESTING"] = True
    response = get(app.test_client(), "/orders/export", key="")
    assert response.status_code == 401


# Sales report

def test_daily_sales(client):
    response = get(client, "/reports/sales?start=2024-01-01&end=2024-02-29&group_by=day")

    assert response.status_code == 200
    periods = [(p["period"], p["order_count"], p["revenue_cents"]) for p in response.get_json()["periods"]]
    assert periods == [
        ("2024-01-01", 1, 2000),
        ("2024-01-02", 1, 500),
        ("2024-01-07", 1, 1000),
        ("2024-02-03", 1, 1000),
    ]


def test_default_grouping_is_day(client):
    response = get(client, "/reports/sales?start=2024-01-01&end=2024-01-02")
    assert response.get_json()["group_by"] == "day"


def test_weekly_sales_group_from_monday(client):
    # 2024-01-07 is a Sunday, so it belongs to the week starting 2024-01-01.
    response = get(client, "/reports/sales?start=2024-01-01&end=2024-02-29&group_by=week")

    periods = [(p["period"], p["order_count"], p["revenue_cents"]) for p in response.get_json()["periods"]]
    assert periods == [
        ("2024-01-01", 3, 3500),
        ("2024-01-29", 1, 1000),
    ]


def test_monthly_sales(client):
    response = get(client, "/reports/sales?start=2024-01-01&end=2024-02-29&group_by=month")

    periods = [(p["period"], p["order_count"], p["revenue_cents"]) for p in response.get_json()["periods"]]
    assert periods == [
        ("2024-01", 3, 3500),
        ("2024-02", 1, 1000),
    ]


def test_cancelled_orders_are_excluded(client):
    response = get(client, "/reports/sales?start=2024-01-08&end=2024-01-08")

    assert response.status_code == 200
    assert response.get_json()["periods"] == []


def test_sales_requires_both_dates(client):
    assert get(client, "/reports/sales?start=2024-01-01").status_code == 400


def test_sales_rejects_reversed_range(client):
    assert get(client, "/reports/sales?start=2024-02-01&end=2024-01-01").status_code == 400


def test_sales_rejects_bad_date(client):
    assert get(client, "/reports/sales?start=2024-13-01&end=2024-12-31").status_code == 400


def test_sales_rejects_unknown_group_by(client):
    response = get(client, "/reports/sales?start=2024-01-01&end=2024-01-31&group_by=year;DROP")
    assert response.status_code == 400


# Top products

def test_top_products_ordered_by_revenue(client):
    response = get(client, "/reports/top-products?start=2024-01-01&end=2024-02-29")

    assert response.status_code == 200
    products = [(p["product_name"], p["units_sold"], p["revenue_cents"]) for p in response.get_json()["products"]]
    assert products == [
        ("Widget", 5, 2500),
        ("Gadget", 2, 2000),
    ]


def test_top_products_limit(client):
    response = get(client, "/reports/top-products?start=2024-01-01&end=2024-02-29&limit=1")

    names = [p["product_name"] for p in response.get_json()["products"]]
    assert names == ["Widget"]


def test_top_products_rejects_out_of_range_limit(client):
    assert get(client, "/reports/top-products?start=2024-01-01&end=2024-02-29&limit=0").status_code == 400
    assert get(client, "/reports/top-products?start=2024-01-01&end=2024-02-29&limit=101").status_code == 400


# Order export

def export_ids(client, query):
    response = get(client, f"/orders/export?{query}")
    assert response.status_code == 200
    return [record["id"] for record in response.get_json()]


def test_export_filters_by_status(client):
    assert set(export_ids(client, "status=paid")) == {1, 4, 5}


def test_export_filters_by_customer(client):
    assert set(export_ids(client, "customer_id=2")) == {2, 4}


def test_export_filters_by_date_range(client):
    assert set(export_ids(client, "start=2024-01-01&end=2024-01-31")) == {1, 2, 3, 5}


def test_export_sorts_by_total_descending(client):
    # Ties on total fall back to id in the same direction.
    assert export_ids(client, "sort=total_cents&direction=desc") == [3, 1, 5, 4, 2]


def test_export_sorts_by_total_ascending(client):
    assert export_ids(client, "sort=total_cents&direction=asc") == [2, 4, 5, 1, 3]


def test_export_csv(client):
    response = get(client, "/orders/export?format=csv&status=paid")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert [row["id"] for row in rows] == ["1", "4", "5"]
    assert rows[0]["customer_email"] == "alice@example.com"
    assert rows[0]["total_cents"] == "2000"


def test_export_rejects_unknown_format(client):
    assert get(client, "/orders/export?format=xml").status_code == 400


def test_export_rejects_unknown_sort_column(client):
    assert get(client, "/orders/export?sort=id;DROP TABLE orders").status_code == 400


def test_export_rejects_bad_direction(client):
    assert get(client, "/orders/export?direction=sideways").status_code == 400


def test_export_rejects_non_integer_customer(client):
    assert get(client, "/orders/export?customer_id=abc").status_code == 400
