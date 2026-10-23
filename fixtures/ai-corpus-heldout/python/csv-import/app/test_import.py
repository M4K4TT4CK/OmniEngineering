import io
import sqlite3
import time

import pytest

from app import create_app, validate_row

HEADER = "name,email,phone,country,signup_date\n"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    app.config["TESTING"] = True
    return app.test_client()


def upload(client, text, filename="customers.csv"):
    return client.post("/imports", data={"file": (io.BytesIO(text.encode("utf-8")), filename)})


def wait_for(client, import_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/imports/{import_id}").get_json()
        if body["status"] in ("completed", "failed"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"import did not finish: {body}")
        time.sleep(0.05)


def run_csv(client, text):
    response = upload(client, text)
    assert response.status_code == 202
    return wait_for(client, response.get_json()["id"])


def customer_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT email, name, phone, country, signup_date FROM customers ORDER BY email").fetchall()
    finally:
        conn.close()


def test_valid_rows_are_inserted(client, db_path):
    body = run_csv(
        client,
        HEADER
        + "Alice,alice@example.com,+1 555-0100,US,2024-01-15\n"
        + "Bob,bob@example.com,,DE,2023-07-01\n",
    )

    assert body["status"] == "completed"
    assert body["total_rows"] == 2
    assert body["processed_rows"] == 2
    assert body["inserted_rows"] == 2
    assert body["updated_rows"] == 0
    assert body["failed_rows"] == 0
    assert body["error_count"] == 0
    assert body["errors"] == []
    assert customer_rows(db_path) == [
        ("alice@example.com", "Alice", "+1 555-0100", "US", "2024-01-15"),
        ("bob@example.com", "Bob", None, "DE", "2023-07-01"),
    ]


def test_reimport_updates_existing_customer_by_email(client, db_path):
    run_csv(client, HEADER + "Alice,alice@example.com,,US,2024-01-15\n")
    body = run_csv(client, HEADER + "Alice Smith,ALICE@example.com,,CA,2024-02-01\n")

    assert body["inserted_rows"] == 0
    assert body["updated_rows"] == 1
    assert customer_rows(db_path) == [("alice@example.com", "Alice Smith", None, "CA", "2024-02-01")]


def test_invalid_rows_are_recorded_and_valid_rows_still_import(client, db_path):
    body = run_csv(
        client,
        HEADER
        + "Alice,alice@example.com,,US,2024-01-15\n"
        + "Bob,not-an-email,,US,2024-01-15\n"
        + ",carol@example.com,,US,2024-13-01\n",
    )

    assert body["status"] == "completed"
    assert body["processed_rows"] == 3
    assert body["inserted_rows"] == 1
    assert body["failed_rows"] == 2
    assert body["error_count"] == 3
    found = {(e["row_number"], e["field"]) for e in body["errors"]}
    assert found == {(2, "email"), (3, "name"), (3, "signup_date")}
    assert [row[0] for row in customer_rows(db_path)] == ["alice@example.com"]


def test_missing_columns_fail_the_import(client):
    body = run_csv(client, "name,email\nAlice,alice@example.com\n")

    assert body["status"] == "failed"
    assert "missing columns" in body["fatal_error"]
    assert "country" in body["fatal_error"]
    assert body["total_rows"] == 0


def test_upload_without_file_is_rejected(client):
    response = client.post("/imports", data={})

    assert response.status_code == 400


def test_unknown_import_returns_404(client):
    response = client.get("/imports/does-not-exist")

    assert response.status_code == 404


def test_errors_are_paginated(client):
    text = HEADER + "".join(f"Name{i},bad-email-{i},,US,2024-01-01\n" for i in range(5))
    response = upload(client, text)
    import_id = response.get_json()["id"]
    wait_for(client, import_id)

    body = client.get(f"/imports/{import_id}?limit=2&offset=2").get_json()

    assert body["error_count"] == 5
    assert [e["row_number"] for e in body["errors"]] == [3, 4]


def test_validate_row_normalises_fields():
    record, errors = validate_row(
        {"name": " Alice ", "email": " Alice@Example.COM ", "phone": "", "country": "us", "signup_date": "2024-01-15"}
    )

    assert errors == []
    assert record == {
        "email": "alice@example.com",
        "name": "Alice",
        "phone": None,
        "country": "US",
        "signup_date": "2024-01-15",
    }


def test_validate_row_reports_each_bad_field():
    record, errors = validate_row(
        {"name": "", "email": "x", "phone": "abc", "country": "USA", "signup_date": "15/01/2024"}
    )

    assert record is None
    assert {field for field, _ in errors} == {"name", "email", "phone", "country", "signup_date"}
