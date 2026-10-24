package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
)

const (
	maxUploadBytes  = 64 << 20
	defaultPageSize = 100
	maxPageSize     = 1000
)

// Server wires the HTTP endpoints to the Importer.
type Server struct {
	imp *Importer
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /imports", s.handleCreateImport)
	mux.HandleFunc("GET /imports/{id}", s.handleGetImport)
	mux.HandleFunc("GET /imports/{id}/errors", s.handleListErrors)
	return mux
}

func (s *Server) handleCreateImport(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	file, _, err := r.FormFile("file")
	if errors.Is(err, http.ErrMissingFile) {
		writeError(w, http.StatusBadRequest, `expected a CSV file in the "file" field`)
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "could not read multipart upload (is it larger than 64 MiB?)")
		return
	}
	defer file.Close()

	// The import runs in the background, so the upload has to outlive this request.
	tmp, err := os.CreateTemp("", "customers-*.csv")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not store upload")
		return
	}
	_, copyErr := io.Copy(tmp, file)
	closeErr := tmp.Close()
	if copyErr != nil || closeErr != nil {
		os.Remove(tmp.Name())
		writeError(w, http.StatusInternalServerError, "could not store upload")
		return
	}

	id, err := s.imp.Start(tmp.Name())
	if err != nil {
		os.Remove(tmp.Name())
		writeError(w, http.StatusInternalServerError, "could not start import")
		return
	}
	w.Header().Set("Location", "/imports/"+strconv.FormatInt(id, 10))
	writeJSON(w, http.StatusAccepted, map[string]int64{"import_id": id})
}

func (s *Server) handleGetImport(w http.ResponseWriter, r *http.Request) {
	id, ok := parseImportID(w, r)
	if !ok {
		return
	}
	st, err := s.imp.Status(id)
	if errors.Is(err, sql.ErrNoRows) {
		writeError(w, http.StatusNotFound, "import not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not load import")
		return
	}
	writeJSON(w, http.StatusOK, st)
}

func (s *Server) handleListErrors(w http.ResponseWriter, r *http.Request) {
	id, ok := parseImportID(w, r)
	if !ok {
		return
	}
	if _, err := s.imp.Status(id); errors.Is(err, sql.ErrNoRows) {
		writeError(w, http.StatusNotFound, "import not found")
		return
	} else if err != nil {
		writeError(w, http.StatusInternalServerError, "could not load import")
		return
	}
	limit, offset, err := pageParams(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	errs, err := s.imp.Errors(id, limit, offset)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "could not load errors")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"import_id": id,
		"limit":     limit,
		"offset":    offset,
		"errors":    errs,
	})
}

func parseImportID(w http.ResponseWriter, r *http.Request) (int64, bool) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || id <= 0 {
		writeError(w, http.StatusBadRequest, "invalid import id")
		return 0, false
	}
	return id, true
}

func pageParams(r *http.Request) (limit, offset int, err error) {
	limit = defaultPageSize
	if v := r.URL.Query().Get("limit"); v != "" {
		limit, err = strconv.Atoi(v)
		if err != nil || limit < 1 {
			return 0, 0, errors.New("limit must be a positive integer")
		}
	}
	if limit > maxPageSize {
		limit = maxPageSize
	}
	if v := r.URL.Query().Get("offset"); v != "" {
		offset, err = strconv.Atoi(v)
		if err != nil || offset < 0 {
			return 0, 0, errors.New("offset must be a non-negative integer")
		}
	}
	return limit, offset, nil
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("writing response: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func main() {
	addr := flag.String("addr", ":8080", "HTTP listen address")
	dbPath := flag.String("db", "customers.db", "SQLite database file")
	flag.Parse()

	db, err := OpenDB(*dbPath)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	srv := &Server{imp: NewImporter(db)}
	log.Printf("listening on %s, database %s", *addr, *dbPath)
	log.Fatal(http.ListenAndServe(*addr, srv.Routes()))
}
