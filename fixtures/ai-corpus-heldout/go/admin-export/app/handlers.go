package main

import (
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const dateLayout = "2006-01-02"

// groupExprs maps the group parameter to a SQLite expression over orders.created_at.
// Weeks start on Monday and use strftime's %W numbering, which is not ISO 8601.
var groupExprs = map[string]string{
	"day":   "strftime('%Y-%m-%d', o.created_at)",
	"week":  "strftime('%Y-W%W', o.created_at)",
	"month": "strftime('%Y-%m', o.created_at)",
}

// exportSortColumns maps the sort parameter to an ORDER BY expression.
// Only these keys ever reach the SQL text, so callers cannot inject SQL.
var exportSortColumns = map[string]string{
	"id":         "o.id",
	"created_at": "o.created_at",
	"status":     "o.status",
	"customer":   "c.name",
	"total":      "total_cents",
}

// dateRange is a half-open window [From, ToExclusive) over created_at.
// An empty bound means unbounded. The request's "to" is inclusive, so it is
// stored as the following day. Comparing YYYY-MM-DD text works for both
// date-only and full timestamp values in created_at.
type dateRange struct {
	From        string
	ToExclusive string
}

func parseRange(q url.Values, required bool) (dateRange, error) {
	var r dateRange
	fromStr, toStr := q.Get("from"), q.Get("to")
	if required && (fromStr == "" || toStr == "") {
		return r, errors.New("from and to are required (YYYY-MM-DD)")
	}
	if fromStr != "" {
		from, err := time.Parse(dateLayout, fromStr)
		if err != nil {
			return r, errors.New("from must be YYYY-MM-DD")
		}
		r.From = from.Format(dateLayout)
	}
	if toStr != "" {
		to, err := time.Parse(dateLayout, toStr)
		if err != nil {
			return r, errors.New("to must be YYYY-MM-DD")
		}
		if fromStr != "" && toStr < fromStr {
			return r, errors.New("to must not be before from")
		}
		r.ToExclusive = to.AddDate(0, 0, 1).Format(dateLayout)
	}
	return r, nil
}

func (r dateRange) conditions(column string) ([]string, []any) {
	var conds []string
	var args []any
	if r.From != "" {
		conds = append(conds, column+" >= ?")
		args = append(args, r.From)
	}
	if r.ToExclusive != "" {
		conds = append(conds, column+" < ?")
		args = append(args, r.ToExclusive)
	}
	return conds, args
}

type salesBucket struct {
	Period       string `json:"period,omitempty"`
	Orders       int64  `json:"orders"`
	Units        int64  `json:"units"`
	RevenueCents int64  `json:"revenue_cents"`
}

type salesReport struct {
	Group   string        `json:"group"`
	From    string        `json:"from"`
	To      string        `json:"to"`
	Buckets []salesBucket `json:"buckets"`
	Totals  salesBucket   `json:"totals"`
}

// handleSalesReport returns revenue for a date range grouped by day, week or
// month. Cancelled orders are excluded.
func (s *server) handleSalesReport(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()

	group := q.Get("group")
	if group == "" {
		group = "day"
	}
	groupExpr, ok := groupExprs[group]
	if !ok {
		writeError(w, http.StatusBadRequest, "group must be day, week or month")
		return
	}
	rng, err := parseRange(q, true)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	conds, args := rng.conditions("o.created_at")

	query := fmt.Sprintf(`
		SELECT %s AS period,
		       COUNT(DISTINCT o.id),
		       SUM(oi.quantity),
		       SUM(oi.quantity * oi.unit_price_cents)
		FROM orders o
		JOIN order_items oi ON oi.order_id = o.id
		WHERE o.status <> 'cancelled' AND %s
		GROUP BY period
		ORDER BY period`, groupExpr, strings.Join(conds, " AND "))

	rows, err := s.db.QueryContext(r.Context(), query, args...)
	if err != nil {
		internalError(w, err)
		return
	}
	defer rows.Close()

	report := salesReport{
		Group:   group,
		From:    q.Get("from"),
		To:      q.Get("to"),
		Buckets: []salesBucket{},
	}
	for rows.Next() {
		var b salesBucket
		if err := rows.Scan(&b.Period, &b.Orders, &b.Units, &b.RevenueCents); err != nil {
			internalError(w, err)
			return
		}
		report.Buckets = append(report.Buckets, b)
		report.Totals.Orders += b.Orders
		report.Totals.Units += b.Units
		report.Totals.RevenueCents += b.RevenueCents
	}
	if err := rows.Err(); err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, report)
}

type productRow struct {
	ProductID    int64  `json:"product_id"`
	Name         string `json:"name"`
	Units        int64  `json:"units"`
	RevenueCents int64  `json:"revenue_cents"`
}

type topProductsReport struct {
	From     string       `json:"from"`
	To       string       `json:"to"`
	Products []productRow `json:"products"`
}

// handleTopProducts returns the highest-revenue products in a date range.
// Cancelled orders are excluded. limit defaults to 10 and is capped at 100.
func (s *server) handleTopProducts(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()

	rng, err := parseRange(q, true)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	limit := 10
	if v := q.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 100 {
			writeError(w, http.StatusBadRequest, "limit must be between 1 and 100")
			return
		}
		limit = n
	}
	conds, args := rng.conditions("o.created_at")
	args = append(args, limit)

	query := fmt.Sprintf(`
		SELECT p.id, p.name,
		       SUM(oi.quantity),
		       SUM(oi.quantity * oi.unit_price_cents) AS revenue_cents
		FROM order_items oi
		JOIN orders o ON o.id = oi.order_id
		JOIN products p ON p.id = oi.product_id
		WHERE o.status <> 'cancelled' AND %s
		GROUP BY p.id, p.name
		ORDER BY revenue_cents DESC, p.id ASC
		LIMIT ?`, strings.Join(conds, " AND "))

	rows, err := s.db.QueryContext(r.Context(), query, args...)
	if err != nil {
		internalError(w, err)
		return
	}
	defer rows.Close()

	report := topProductsReport{From: q.Get("from"), To: q.Get("to"), Products: []productRow{}}
	for rows.Next() {
		var p productRow
		if err := rows.Scan(&p.ProductID, &p.Name, &p.Units, &p.RevenueCents); err != nil {
			internalError(w, err)
			return
		}
		report.Products = append(report.Products, p)
	}
	if err := rows.Err(); err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, report)
}

type exportRow struct {
	ID           int64  `json:"id"`
	CreatedAt    string `json:"created_at"`
	Status       string `json:"status"`
	CustomerID   int64  `json:"customer_id"`
	CustomerName string `json:"customer_name"`
	TotalCents   int64  `json:"total_cents"`
}

// handleOrderExport streams orders as CSV or JSON.
//
// Query parameters (all optional):
//
//	format      csv | json (default json)
//	status      exact order status
//	customer_id integer customer id
//	from, to    YYYY-MM-DD, inclusive
//	sort        id | created_at | status | customer | total (default created_at)
//	dir         asc | desc (default asc)
func (s *server) handleOrderExport(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()

	format := q.Get("format")
	if format == "" {
		format = "json"
	}
	if format != "csv" && format != "json" {
		writeError(w, http.StatusBadRequest, "format must be csv or json")
		return
	}

	sortKey := q.Get("sort")
	if sortKey == "" {
		sortKey = "created_at"
	}
	sortExpr, ok := exportSortColumns[sortKey]
	if !ok {
		writeError(w, http.StatusBadRequest, "sort must be one of id, created_at, status, customer, total")
		return
	}
	direction := "ASC"
	switch q.Get("dir") {
	case "", "asc":
	case "desc":
		direction = "DESC"
	default:
		writeError(w, http.StatusBadRequest, "dir must be asc or desc")
		return
	}

	conds := []string{"1 = 1"}
	var args []any
	if status := q.Get("status"); status != "" {
		conds = append(conds, "o.status = ?")
		args = append(args, status)
	}
	if v := q.Get("customer_id"); v != "" {
		id, err := strconv.ParseInt(v, 10, 64)
		if err != nil {
			writeError(w, http.StatusBadRequest, "customer_id must be an integer")
			return
		}
		conds = append(conds, "o.customer_id = ?")
		args = append(args, id)
	}
	rng, err := parseRange(q, false)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	rangeConds, rangeArgs := rng.conditions("o.created_at")
	conds = append(conds, rangeConds...)
	args = append(args, rangeArgs...)

	query := fmt.Sprintf(`
		SELECT o.id, o.created_at, o.status, o.customer_id, c.name,
		       (SELECT COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0)
		        FROM order_items oi WHERE oi.order_id = o.id) AS total_cents
		FROM orders o
		JOIN customers c ON c.id = o.customer_id
		WHERE %s
		ORDER BY %s %s, o.id ASC`, strings.Join(conds, " AND "), sortExpr, direction)

	rows, err := s.db.QueryContext(r.Context(), query, args...)
	if err != nil {
		internalError(w, err)
		return
	}
	defer rows.Close()

	// Headers are sent before rows are read, so a failure partway through
	// can only be logged, not reported to the caller.
	if format == "csv" {
		writeOrdersCSV(w, rows)
	} else {
		writeOrdersJSON(w, rows)
	}
}

func writeOrdersCSV(w http.ResponseWriter, rows *sql.Rows) {
	w.Header().Set("Content-Type", "text/csv")
	w.Header().Set("Content-Disposition", `attachment; filename="orders.csv"`)

	cw := csv.NewWriter(w)
	cw.Write([]string{"id", "created_at", "status", "customer_id", "customer_name", "total_cents"})
	for rows.Next() {
		var row exportRow
		if err := rows.Scan(&row.ID, &row.CreatedAt, &row.Status, &row.CustomerID, &row.CustomerName, &row.TotalCents); err != nil {
			log.Printf("export scan: %v", err)
			break
		}
		cw.Write([]string{
			strconv.FormatInt(row.ID, 10),
			row.CreatedAt,
			row.Status,
			strconv.FormatInt(row.CustomerID, 10),
			row.CustomerName,
			strconv.FormatInt(row.TotalCents, 10),
		})
	}
	cw.Flush()
	if err := rows.Err(); err != nil {
		log.Printf("export rows: %v", err)
	}
}

func writeOrdersJSON(w http.ResponseWriter, rows *sql.Rows) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition", `attachment; filename="orders.json"`)

	w.Write([]byte("["))
	first := true
	for rows.Next() {
		var row exportRow
		if err := rows.Scan(&row.ID, &row.CreatedAt, &row.Status, &row.CustomerID, &row.CustomerName, &row.TotalCents); err != nil {
			log.Printf("export scan: %v", err)
			break
		}
		b, err := json.Marshal(row)
		if err != nil {
			log.Printf("export marshal: %v", err)
			break
		}
		if !first {
			w.Write([]byte(","))
		}
		first = false
		w.Write(b)
	}
	w.Write([]byte("]"))
	if err := rows.Err(); err != nil {
		log.Printf("export rows: %v", err)
	}
}
