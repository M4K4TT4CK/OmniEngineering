package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

const csvHeader = "name,email,phone,country,signup_date\n"

func newTestImporter(t *testing.T) (*Importer, *sql.DB) {
	t.Helper()
	db, err := OpenDB(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return NewImporter(db), db
}

func writeCSV(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "customers.csv")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// runImport starts an import of body and waits for it to leave the "running" state.
func runImport(t *testing.T, im *Importer, body string) ImportStatus {
	t.Helper()
	id, err := im.Start(writeCSV(t, body))
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		st, err := im.Status(id)
		if err != nil {
			t.Fatal(err)
		}
		if st.Status != "running" {
			return st
		}
		if time.Now().After(deadline) {
			t.Fatal("import did not finish in time")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestImportInsertsAndUpdatesByEmail(t *testing.T) {
	im, db := newTestImporter(t)

	first := runImport(t, im, csvHeader+
		"Ada Lovelace,ada@example.com,+44 20 7946 0000,gb,1815-12-10\n"+
		"Grace Hopper,grace@example.com,,US,1906-12-09\n")
	if first.Status != "completed" || first.Inserted != 2 || first.Updated != 0 || first.FailedRows != 0 {
		t.Fatalf("first import: got %+v", first)
	}
	if first.TotalRows != 2 || first.ProcessedRows != 2 {
		t.Fatalf("first import progress: got total=%d processed=%d", first.TotalRows, first.ProcessedRows)
	}

	// Same email with different case and name: should update, not insert.
	second := runImport(t, im, csvHeader+"Ada King,ADA@example.com,,GB,1815-12-10\n")
	if second.Status != "completed" || second.Inserted != 0 || second.Updated != 1 {
		t.Fatalf("second import: got %+v", second)
	}

	var name string
	if err := db.QueryRow(`SELECT name FROM customers WHERE email = ?`, "ada@example.com").Scan(&name); err != nil {
		t.Fatal(err)
	}
	if name != "Ada King" {
		t.Errorf("name: got %q, want %q", name, "Ada King")
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM customers`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Errorf("customer count: got %d, want 2", count)
	}
}

func TestImportRecordsRowErrors(t *testing.T) {
	im, db := newTestImporter(t)

	st := runImport(t, im, csvHeader+
		"Valid,valid@example.com,555-0100,US,2024-01-02\n"+ // row 1: ok
		",missing@example.com,,US,2024-01-02\n"+ // row 2: no name
		"Bad Email,not-an-email,,US,2024-01-02\n"+ // row 3: bad email
		"Bad Country,bad@example.com,,USA,2024-01-02\n"+ // row 4: country too long
		"Bad Date,date@example.com,,US,02/01/2024\n"+ // row 5: wrong date format
		"Bad Phone,phone@example.com,call me,US,2024-01-02\n") // row 6: bad phone
	if st.Status != "completed" || st.Inserted != 1 || st.FailedRows != 5 || st.ProcessedRows != 6 {
		t.Fatalf("import: got %+v", st)
	}

	errs, err := im.Errors(st.ID, 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	wantRows := []int{2, 3, 4, 5, 6}
	if len(errs) != len(wantRows) {
		t.Fatalf("errors: got %d, want %d: %+v", len(errs), len(wantRows), errs)
	}
	for i, want := range wantRows {
		if errs[i].Row != want {
			t.Errorf("error %d: row %d, want %d", i, errs[i].Row, want)
		}
	}
	if !strings.Contains(errs[0].Message, "name is required") {
		t.Errorf("row 2 message: %q", errs[0].Message)
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM customers`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Errorf("customer count: got %d, want 1", count)
	}
}

func TestImportErrorsPaginate(t *testing.T) {
	im, _ := newTestImporter(t)

	st := runImport(t, im, csvHeader+
		",a@example.com,,US,2024-01-02\n"+
		",b@example.com,,US,2024-01-02\n"+
		",c@example.com,,US,2024-01-02\n")

	page, err := im.Errors(st.ID, 2, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(page) != 1 || page[0].Row != 3 {
		t.Fatalf("page: got %+v, want one error at row 3", page)
	}
}

func TestImportFailsOnMissingColumn(t *testing.T) {
	im, db := newTestImporter(t)

	st := runImport(t, im, "name,email,country,signup_date\nAda,ada@example.com,US,2024-01-02\n")
	if st.Status != "failed" {
		t.Fatalf("status: got %q, want failed", st.Status)
	}
	if !strings.Contains(st.FatalError, "phone") {
		t.Errorf("fatal error: got %q, want it to name the phone column", st.FatalError)
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM customers`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Errorf("customer count: got %d, want 0", count)
	}
}

func TestHTTPUploadAndProgress(t *testing.T) {
	im, _ := newTestImporter(t)
	srv := httptest.NewServer((&Server{imp: im}).Routes())
	defer srv.Close()

	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	fw, err := mw.CreateFormFile("file", "customers.csv")
	if err != nil {
		t.Fatal(err)
	}
	fw.Write([]byte(csvHeader + "Ada,ada@example.com,,GB,1815-12-10\nBad,nope,,GB,1815-12-10\n"))
	mw.Close()

	resp, err := http.Post(srv.URL+"/imports", mw.FormDataContentType(), &body)
	if err != nil {
		t.Fatal(err)
	}
	var created struct {
		ImportID int64 `json:"import_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted || created.ImportID == 0 {
		t.Fatalf("create: status %d, id %d", resp.StatusCode, created.ImportID)
	}

	statusURL := srv.URL + "/imports/" + strconv.FormatInt(created.ImportID, 10)
	var st ImportStatus
	deadline := time.Now().Add(5 * time.Second)
	for {
		getJSON(t, statusURL, &st)
		if st.Status != "running" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("import did not finish in time")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if st.Status != "completed" || st.Inserted != 1 || st.FailedRows != 1 {
		t.Fatalf("status: got %+v", st)
	}

	var errs struct {
		Errors []RowError `json:"errors"`
	}
	if code := getJSON(t, statusURL+"/errors", &errs); code != http.StatusOK {
		t.Fatalf("errors: status %d", code)
	}
	if len(errs.Errors) != 1 || errs.Errors[0].Row != 2 {
		t.Fatalf("errors: got %+v", errs.Errors)
	}

	if code := getJSON(t, srv.URL+"/imports/999", nil); code != http.StatusNotFound {
		t.Errorf("unknown import: status %d, want 404", code)
	}

	noFile, err := http.Post(srv.URL+"/imports", "text/plain", strings.NewReader("x"))
	if err != nil {
		t.Fatal(err)
	}
	noFile.Body.Close()
	if noFile.StatusCode != http.StatusBadRequest {
		t.Errorf("missing file: status %d, want 400", noFile.StatusCode)
	}
}

// getJSON fetches url and decodes a 200 response into v (if v is non-nil). It returns the status code.
func getJSON(t *testing.T, url string, v any) int {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if v != nil && resp.StatusCode == http.StatusOK {
		if err := json.NewDecoder(resp.Body).Decode(v); err != nil {
			t.Fatal(err)
		}
	}
	return resp.StatusCode
}
