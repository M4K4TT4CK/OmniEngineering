package main

import (
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"reflect"
	"testing"
)

const testSchema = `
CREATE TABLE admin_api_keys (key_hash TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, sku TEXT NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                     status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE order_items (order_id INTEGER NOT NULL REFERENCES orders(id),
                          product_id INTEGER NOT NULL REFERENCES products(id),
                          quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL);
`

// Order 3 is cancelled: it must be excluded from reports but appears in exports.
const testSeed = `
INSERT INTO customers (id, name, email) VALUES (1, 'Ada', 'ada@example.com'), (2, 'Grace', 'grace@example.com');
INSERT INTO products (id, name, sku) VALUES (1, 'Widget', 'W-1'), (2, 'Gadget', 'G-1');
INSERT INTO orders (id, customer_id, status, created_at) VALUES
  (1, 1, 'paid', '2026-01-05 10:00:00'),
  (2, 2, 'shipped', '2026-01-06 09:00:00'),
  (3, 1, 'cancelled', '2026-01-06 12:00:00'),
  (4, 2, 'paid', '2026-02-10 08:00:00');
INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents) VALUES
  (1, 1, 2, 500), (1, 2, 1, 2500),
  (2, 1, 1, 500),
  (3, 2, 5, 2500),
  (4, 2, 2, 2500);
`

func setup(t *testing.T) (http.Handler, string) {
	t.Helper()
	db, err := sql.Open("sqlite", filepath.Join(t.TempDir(), "shop.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })

	if _, err := db.Exec(testSchema + testSeed); err != nil {
		t.Fatal(err)
	}
	key := "test-admin-key"
	if _, err := db.Exec(`INSERT INTO admin_api_keys (key_hash, name, created_at) VALUES (?, 'test', '2026-01-01')`, hashKey(key)); err != nil {
		t.Fatal(err)
	}
	return newServer(db).routes(), key
}

func get(t *testing.T, h http.Handler, key, target string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, target, nil)
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func expectStatus(t *testing.T, rec *httptest.ResponseRecorder, want int) {
	t.Helper()
	if rec.Code != want {
		t.Fatalf("status = %d, want %d; body: %s", rec.Code, want, rec.Body.String())
	}
}

func decode(t *testing.T, rec *httptest.ResponseRecorder, v any) {
	t.Helper()
	expectStatus(t, rec, http.StatusOK)
	if err := json.NewDecoder(rec.Body).Decode(v); err != nil {
		t.Fatal(err)
	}
}

func TestRequiresAPIKey(t *testing.T) {
	h, key := setup(t)
	const target = "/api/reports/sales?from=2026-01-01&to=2026-01-31"

	expectStatus(t, get(t, h, "", target), http.StatusUnauthorized)
	expectStatus(t, get(t, h, "wrong-key", target), http.StatusUnauthorized)
	expectStatus(t, get(t, h, key, target), http.StatusOK)
}

func TestAPIKeyViaHeader(t *testing.T) {
	h, key := setup(t)
	req := httptest.NewRequest(http.MethodGet, "/api/reports/top-products?from=2026-01-01&to=2026-12-31", nil)
	req.Header.Set("X-API-Key", key)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	expectStatus(t, rec, http.StatusOK)
}

func TestSalesReportByDay(t *testing.T) {
	h, key := setup(t)
	var report salesReport
	decode(t, get(t, h, key, "/api/reports/sales?from=2026-01-01&to=2026-02-28&group=day"), &report)

	want := []salesBucket{
		{Period: "2026-01-05", Orders: 1, Units: 3, RevenueCents: 3500},
		{Period: "2026-01-06", Orders: 1, Units: 1, RevenueCents: 500},
		{Period: "2026-02-10", Orders: 1, Units: 2, RevenueCents: 5000},
	}
	if !reflect.DeepEqual(report.Buckets, want) {
		t.Errorf("buckets = %+v, want %+v", report.Buckets, want)
	}
	wantTotals := salesBucket{Orders: 3, Units: 6, RevenueCents: 9000}
	if report.Totals != wantTotals {
		t.Errorf("totals = %+v, want %+v", report.Totals, wantTotals)
	}
}

func TestSalesReportByMonth(t *testing.T) {
	h, key := setup(t)
	var report salesReport
	decode(t, get(t, h, key, "/api/reports/sales?from=2026-01-01&to=2026-02-28&group=month"), &report)

	want := []salesBucket{
		{Period: "2026-01", Orders: 2, Units: 4, RevenueCents: 4000},
		{Period: "2026-02", Orders: 1, Units: 2, RevenueCents: 5000},
	}
	if !reflect.DeepEqual(report.Buckets, want) {
		t.Errorf("buckets = %+v, want %+v", report.Buckets, want)
	}
}

func TestSalesReportByWeek(t *testing.T) {
	h, key := setup(t)
	var report salesReport
	decode(t, get(t, h, key, "/api/reports/sales?from=2026-01-01&to=2026-02-28&group=week"), &report)

	// Jan 5 and Jan 6 fall in the same Monday-start week; Feb 10 is in week 06.
	want := []salesBucket{
		{Period: "2026-W01", Orders: 2, Units: 4, RevenueCents: 4000},
		{Period: "2026-W06", Orders: 1, Units: 2, RevenueCents: 5000},
	}
	if !reflect.DeepEqual(report.Buckets, want) {
		t.Errorf("buckets = %+v, want %+v", report.Buckets, want)
	}
}

func TestSalesReportToIsInclusive(t *testing.T) {
	h, key := setup(t)
	var report salesReport
	decode(t, get(t, h, key, "/api/reports/sales?from=2026-02-10&to=2026-02-10&group=day"), &report)

	if len(report.Buckets) != 1 || report.Buckets[0].Period != "2026-02-10" {
		t.Errorf("buckets = %+v, want only 2026-02-10", report.Buckets)
	}
}

func TestSalesReportEmptyRange(t *testing.T) {
	h, key := setup(t)
	var report salesReport
	decode(t, get(t, h, key, "/api/reports/sales?from=2030-01-01&to=2030-01-31"), &report)

	if report.Buckets == nil || len(report.Buckets) != 0 {
		t.Errorf("buckets = %#v, want empty non-nil slice", report.Buckets)
	}
}

func TestSalesReportValidation(t *testing.T) {
	h, key := setup(t)
	cases := []string{
		"/api/reports/sales?from=2026-01-01&to=2026-01-31&group=year",
		"/api/reports/sales?from=2026-01-01",
		"/api/reports/sales?from=01/01/2026&to=2026-01-31",
		"/api/reports/sales?from=2026-02-01&to=2026-01-01",
	}
	for _, target := range cases {
		expectStatus(t, get(t, h, key, target), http.StatusBadRequest)
	}
}

func TestTopProducts(t *testing.T) {
	h, key := setup(t)
	var report topProductsReport
	decode(t, get(t, h, key, "/api/reports/top-products?from=2026-01-01&to=2026-12-31"), &report)

	want := []productRow{
		{ProductID: 2, Name: "Gadget", Units: 3, RevenueCents: 7500},
		{ProductID: 1, Name: "Widget", Units: 3, RevenueCents: 1500},
	}
	if !reflect.DeepEqual(report.Products, want) {
		t.Errorf("products = %+v, want %+v", report.Products, want)
	}
}

func TestTopProductsLimit(t *testing.T) {
	h, key := setup(t)
	var report topProductsReport
	decode(t, get(t, h, key, "/api/reports/top-products?from=2026-01-01&to=2026-12-31&limit=1"), &report)

	if len(report.Products) != 1 || report.Products[0].ProductID != 2 {
		t.Errorf("products = %+v, want only Gadget", report.Products)
	}
	expectStatus(t, get(t, h, key, "/api/reports/top-products?from=2026-01-01&to=2026-12-31&limit=0"), http.StatusBadRequest)
	expectStatus(t, get(t, h, key, "/api/reports/top-products?from=2026-01-01&to=2026-12-31&limit=101"), http.StatusBadRequest)
}

func TestExportCSVFilteredAndSorted(t *testing.T) {
	h, key := setup(t)
	rec := get(t, h, key, "/api/orders/export?format=csv&status=paid&sort=total&dir=desc")
	expectStatus(t, rec, http.StatusOK)

	records, err := csv.NewReader(rec.Body).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 3 {
		t.Fatalf("records = %d, want header + 2 rows: %v", len(records), records)
	}
	if records[0][0] != "id" || records[0][5] != "total_cents" {
		t.Errorf("header = %v", records[0])
	}
	// Order 4 totals 5000 and order 1 totals 3500, so desc puts 4 first.
	if records[1][0] != "4" || records[2][0] != "1" {
		t.Errorf("order ids = %s, %s; want 4, 1", records[1][0], records[2][0])
	}
	if records[1][5] != "5000" {
		t.Errorf("total_cents = %s, want 5000", records[1][5])
	}
}

func TestExportJSONCustomerFilter(t *testing.T) {
	h, key := setup(t)
	var rows []exportRow
	decode(t, get(t, h, key, "/api/orders/export?customer_id=1"), &rows)

	// Default sort is created_at ascending. Cancelled orders are included.
	if len(rows) != 2 || rows[0].ID != 1 || rows[1].ID != 3 {
		t.Fatalf("rows = %+v, want orders 1 and 3", rows)
	}
	if rows[0].CustomerName != "Ada" || rows[0].TotalCents != 3500 {
		t.Errorf("row 0 = %+v", rows[0])
	}
}

func TestExportJSONDateRange(t *testing.T) {
	h, key := setup(t)
	var rows []exportRow
	decode(t, get(t, h, key, "/api/orders/export?from=2026-01-06&to=2026-01-06&sort=id&dir=desc"), &rows)

	if len(rows) != 2 || rows[0].ID != 3 || rows[1].ID != 2 {
		t.Errorf("rows = %+v, want orders 3 and 2", rows)
	}
}

func TestExportEmptyResultIsArray(t *testing.T) {
	h, key := setup(t)
	rec := get(t, h, key, "/api/orders/export?status=refunded")
	expectStatus(t, rec, http.StatusOK)
	if got := rec.Body.String(); got != "[]" {
		t.Errorf("body = %q, want []", got)
	}
}

func TestExportValidation(t *testing.T) {
	h, key := setup(t)
	cases := []string{
		"/api/orders/export?format=xml",
		"/api/orders/export?sort=email",
		"/api/orders/export?sort=id%3BDROP%20TABLE%20orders",
		"/api/orders/export?dir=sideways",
		"/api/orders/export?customer_id=abc",
		"/api/orders/export?from=2026-13-01",
	}
	for _, target := range cases {
		expectStatus(t, get(t, h, key, target), http.StatusBadRequest)
	}
}
