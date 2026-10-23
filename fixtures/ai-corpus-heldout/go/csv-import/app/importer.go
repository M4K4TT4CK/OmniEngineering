package main

import (
	"database/sql"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"log"
	"net/mail"
	"os"
	"regexp"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

const dateLayout = "2006-01-02"

var (
	phonePattern   = regexp.MustCompile(`^[0-9+()\- ]{1,32}$`)
	countryPattern = regexp.MustCompile(`^[A-Za-z]{2}$`)
)

var requiredColumns = []string{"name", "email", "phone", "country", "signup_date"}

var schema = []string{
	`CREATE TABLE IF NOT EXISTS customers (
		id INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		email TEXT NOT NULL UNIQUE,
		phone TEXT NOT NULL DEFAULT '',
		country TEXT NOT NULL,
		signup_date TEXT NOT NULL
	)`,
	`CREATE TABLE IF NOT EXISTS imports (
		id INTEGER PRIMARY KEY,
		status TEXT NOT NULL,
		total_rows INTEGER NOT NULL DEFAULT 0,
		processed_rows INTEGER NOT NULL DEFAULT 0,
		inserted INTEGER NOT NULL DEFAULT 0,
		updated INTEGER NOT NULL DEFAULT 0,
		failed_rows INTEGER NOT NULL DEFAULT 0,
		fatal_error TEXT NOT NULL DEFAULT '',
		created_at TEXT NOT NULL,
		finished_at TEXT NOT NULL DEFAULT ''
	)`,
	`CREATE TABLE IF NOT EXISTS import_errors (
		id INTEGER PRIMARY KEY,
		import_id INTEGER NOT NULL REFERENCES imports(id),
		row_num INTEGER NOT NULL,
		message TEXT NOT NULL
	)`,
	`CREATE INDEX IF NOT EXISTS import_errors_by_import ON import_errors (import_id, row_num)`,
}

// Customer is one validated row from an import file.
type Customer struct {
	Name       string
	Email      string
	Phone      string
	Country    string
	SignupDate string
}

// ImportStatus is the progress of one import, as returned by the API.
type ImportStatus struct {
	ID            int64  `json:"id"`
	Status        string `json:"status"`
	TotalRows     int    `json:"total_rows"`
	ProcessedRows int    `json:"processed_rows"`
	Inserted      int    `json:"inserted"`
	Updated       int    `json:"updated"`
	FailedRows    int    `json:"failed_rows"`
	FatalError    string `json:"fatal_error,omitempty"`
	CreatedAt     string `json:"created_at"`
	FinishedAt    string `json:"finished_at,omitempty"`
}

// RowError describes why one data row was rejected. Row 1 is the first row after the header.
type RowError struct {
	Row     int    `json:"row"`
	Message string `json:"message"`
}

type progress struct {
	processed int
	inserted  int
	updated   int
	failed    int
}

// OpenDB opens (and creates if needed) the SQLite database at path.
func OpenDB(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// SQLite allows a single writer; one connection avoids "database is locked" errors.
	db.SetMaxOpenConns(1)
	for _, stmt := range schema {
		if _, err := db.Exec(stmt); err != nil {
			db.Close()
			return nil, fmt.Errorf("creating schema: %w", err)
		}
	}
	return db, nil
}

// Importer runs CSV imports in the background and tracks their progress.
type Importer struct {
	db *sql.DB
}

func NewImporter(db *sql.DB) *Importer {
	return &Importer{db: db}
}

// Start records a new import and processes the file at path in the background.
// The file is removed when the import finishes.
func (im *Importer) Start(path string) (int64, error) {
	res, err := im.db.Exec(`INSERT INTO imports (status, created_at) VALUES ('running', ?)`, nowUTC())
	if err != nil {
		return 0, err
	}
	id, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	go im.run(id, path)
	return id, nil
}

// Status returns the progress of an import. It returns sql.ErrNoRows if the import does not exist.
func (im *Importer) Status(id int64) (ImportStatus, error) {
	var st ImportStatus
	err := im.db.QueryRow(`
		SELECT id, status, total_rows, processed_rows, inserted, updated, failed_rows, fatal_error, created_at, finished_at
		FROM imports WHERE id = ?`, id).Scan(
		&st.ID, &st.Status, &st.TotalRows, &st.ProcessedRows, &st.Inserted,
		&st.Updated, &st.FailedRows, &st.FatalError, &st.CreatedAt, &st.FinishedAt,
	)
	return st, err
}

// Errors returns one page of row errors for an import, ordered by row.
func (im *Importer) Errors(id int64, limit, offset int) ([]RowError, error) {
	rows, err := im.db.Query(
		`SELECT row_num, message FROM import_errors WHERE import_id = ? ORDER BY row_num LIMIT ? OFFSET ?`,
		id, limit, offset,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	errs := []RowError{}
	for rows.Next() {
		var e RowError
		if err := rows.Scan(&e.Row, &e.Message); err != nil {
			return nil, err
		}
		errs = append(errs, e)
	}
	return errs, rows.Err()
}

func (im *Importer) run(id int64, path string) {
	defer os.Remove(path)

	if err := im.process(id, path); err != nil {
		im.finish(id, "failed", err.Error())
		return
	}
	im.finish(id, "completed", "")
}

func (im *Importer) finish(id int64, status, fatalErr string) {
	_, err := im.db.Exec(
		`UPDATE imports SET status = ?, fatal_error = ?, finished_at = ? WHERE id = ?`,
		status, fatalErr, nowUTC(), id,
	)
	if err != nil {
		log.Printf("import %d: recording finish: %v", id, err)
	}
}

// process reads the file row by row. Bad rows are recorded as row errors and skipped;
// a missing header or a database failure stops the whole import.
func (im *Importer) process(id int64, path string) error {
	total, err := countRows(path)
	if err != nil {
		return err
	}
	if _, err := im.db.Exec(`UPDATE imports SET total_rows = ? WHERE id = ?`, total, id); err != nil {
		return err
	}

	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	r := csv.NewReader(f)
	r.FieldsPerRecord = -1
	r.TrimLeadingSpace = true

	header, err := r.Read()
	if errors.Is(err, io.EOF) {
		return errors.New("file is empty: expected a header row")
	}
	if err != nil {
		return fmt.Errorf("reading header: %w", err)
	}
	cols, err := columnIndex(header)
	if err != nil {
		return err
	}

	var p progress
	for row := 1; ; row++ {
		rec, err := r.Read()
		if errors.Is(err, io.EOF) {
			break
		}

		var cust Customer
		var rowErr error
		if err != nil {
			rowErr = fmt.Errorf("malformed CSV: %v", err)
		} else {
			cust, rowErr = parseRow(rec, cols)
		}

		if rowErr != nil {
			p.failed++
			if err := im.recordError(id, row, rowErr.Error()); err != nil {
				return err
			}
		} else {
			inserted, err := im.upsert(cust)
			if err != nil {
				return err
			}
			if inserted {
				p.inserted++
			} else {
				p.updated++
			}
		}

		p.processed++
		if err := im.saveProgress(id, p); err != nil {
			return err
		}
	}
	return nil
}

// upsert inserts the customer, or updates the existing row with the same email.
// It reports whether a new row was inserted.
func (im *Importer) upsert(c Customer) (bool, error) {
	tx, err := im.db.Begin()
	if err != nil {
		return false, err
	}
	defer tx.Rollback()

	var id int64
	err = tx.QueryRow(`SELECT id FROM customers WHERE email = ?`, c.Email).Scan(&id)
	inserted := errors.Is(err, sql.ErrNoRows)
	if err != nil && !inserted {
		return false, err
	}

	if inserted {
		_, err = tx.Exec(
			`INSERT INTO customers (name, email, phone, country, signup_date) VALUES (?, ?, ?, ?, ?)`,
			c.Name, c.Email, c.Phone, c.Country, c.SignupDate,
		)
	} else {
		_, err = tx.Exec(
			`UPDATE customers SET name = ?, phone = ?, country = ?, signup_date = ? WHERE id = ?`,
			c.Name, c.Phone, c.Country, c.SignupDate, id,
		)
	}
	if err != nil {
		return false, err
	}
	return inserted, tx.Commit()
}

func (im *Importer) recordError(id int64, row int, msg string) error {
	_, err := im.db.Exec(
		`INSERT INTO import_errors (import_id, row_num, message) VALUES (?, ?, ?)`,
		id, row, msg,
	)
	return err
}

func (im *Importer) saveProgress(id int64, p progress) error {
	_, err := im.db.Exec(
		`UPDATE imports SET processed_rows = ?, inserted = ?, updated = ?, failed_rows = ? WHERE id = ?`,
		p.processed, p.inserted, p.updated, p.failed, id,
	)
	return err
}

// countRows returns the number of data rows in the file, not counting the header.
func countRows(path string) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	r := csv.NewReader(f)
	r.FieldsPerRecord = -1
	n := -1 // starts at -1 so the header is not counted
	for {
		_, err := r.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		n++
	}
	if n < 0 {
		n = 0
	}
	return n, nil
}

// columnIndex maps each required column name to its position in the header.
func columnIndex(header []string) (map[string]int, error) {
	cols := make(map[string]int, len(header))
	for i, h := range header {
		name := strings.ToLower(strings.TrimSpace(strings.TrimPrefix(h, "﻿")))
		cols[name] = i
	}
	var missing []string
	for _, name := range requiredColumns {
		if _, ok := cols[name]; !ok {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		return nil, fmt.Errorf("missing required columns: %s", strings.Join(missing, ", "))
	}
	return cols, nil
}

// parseRow validates one CSV record and returns the customer it describes.
func parseRow(rec []string, cols map[string]int) (Customer, error) {
	field := func(name string) string {
		i := cols[name]
		if i >= len(rec) {
			return ""
		}
		return strings.TrimSpace(rec[i])
	}

	var problems []string
	c := Customer{
		Name:       field("name"),
		Phone:      field("phone"),
		Country:    strings.ToUpper(field("country")),
		SignupDate: field("signup_date"),
	}

	if c.Name == "" {
		problems = append(problems, "name is required")
	} else if len(c.Name) > 200 {
		problems = append(problems, "name is longer than 200 characters")
	}

	raw := field("email")
	switch addr, err := mail.ParseAddress(raw); {
	case raw == "":
		problems = append(problems, "email is required")
	case err != nil || addr.Address != raw:
		problems = append(problems, "email is not a valid address")
	default:
		c.Email = strings.ToLower(raw)
	}

	if c.Phone != "" && !phonePattern.MatchString(c.Phone) {
		problems = append(problems, "phone may only contain digits, spaces, +, - and parentheses (max 32 characters)")
	}

	if !countryPattern.MatchString(c.Country) {
		problems = append(problems, "country must be a 2-letter ISO 3166 code")
	}

	if _, err := time.Parse(dateLayout, c.SignupDate); err != nil {
		problems = append(problems, "signup_date must be in YYYY-MM-DD format")
	}

	if len(problems) > 0 {
		return Customer{}, errors.New(strings.Join(problems, "; "))
	}
	return c, nil
}

func nowUTC() string {
	return time.Now().UTC().Format(time.RFC3339)
}
