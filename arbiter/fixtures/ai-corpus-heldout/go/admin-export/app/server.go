package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"

	_ "modernc.org/sqlite"
)

type server struct {
	db *sql.DB
}

func newServer(db *sql.DB) *server {
	return &server{db: db}
}

func main() {
	dbPath := getenv("DB_PATH", "shop.db")
	addr := getenv("ADDR", ":8080")

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	if err := db.Ping(); err != nil {
		log.Fatal(err)
	}

	log.Printf("admin export listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, newServer(db).routes()))
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/reports/sales", s.handleSalesReport)
	mux.HandleFunc("GET /api/reports/top-products", s.handleTopProducts)
	mux.HandleFunc("GET /api/orders/export", s.handleOrderExport)
	return s.requireAdmin(mux)
}

// requireAdmin accepts "Authorization: Bearer <key>" or "X-API-Key: <key>".
// Keys are stored only as SHA-256 hashes in admin_api_keys.
func (s *server) requireAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := apiKeyFromRequest(r)
		if key == "" {
			writeError(w, http.StatusUnauthorized, "missing API key")
			return
		}
		ok, err := s.isAdminKey(r.Context(), key)
		if err != nil {
			internalError(w, err)
			return
		}
		if !ok {
			writeError(w, http.StatusUnauthorized, "invalid API key")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *server) isAdminKey(ctx context.Context, key string) (bool, error) {
	var n int
	err := s.db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM admin_api_keys WHERE key_hash = ?`, hashKey(key)).Scan(&n)
	return n > 0, err
}

func apiKeyFromRequest(r *http.Request) string {
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
		return strings.TrimSpace(strings.TrimPrefix(h, "Bearer "))
	}
	return strings.TrimSpace(r.Header.Get("X-API-Key"))
}

// hashKey is safe to look up directly: the keys are high-entropy random
// strings, so the hash reveals nothing useful about the key.
func hashKey(key string) string {
	sum := sha256.Sum256([]byte(key))
	return hex.EncodeToString(sum[:])
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func internalError(w http.ResponseWriter, err error) {
	log.Printf("internal error: %v", err)
	writeError(w, http.StatusInternalServerError, "internal error")
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
