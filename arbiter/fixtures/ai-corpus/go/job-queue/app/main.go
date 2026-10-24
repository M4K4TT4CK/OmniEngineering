package main

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

type submitRequest struct {
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload"`
}

func newServer(q *Queue) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("POST /jobs", func(w http.ResponseWriter, r *http.Request) {
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		var req submitRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON body"})
			return
		}
		if len(req.Payload) == 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "payload is required"})
			return
		}
		id, err := q.Submit(req.Type, req.Payload)
		switch {
		case errors.Is(err, ErrQueueFull):
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		case err != nil:
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		default:
			writeJSON(w, http.StatusAccepted, map[string]string{"id": id})
		}
	})

	mux.HandleFunc("GET /jobs/{id}", func(w http.ResponseWriter, r *http.Request) {
		job, ok := q.Get(r.PathValue("id"))
		if !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "job not found"})
			return
		}
		writeJSON(w, http.StatusOK, job)
	})

	return mux
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func envInt(name string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(name)); err == nil && v > 0 {
		return v
	}
	return def
}

func main() {
	root := os.Getenv("IMAGE_ROOT")
	if root == "" {
		root = "."
	}
	q := NewQueue(envInt("WORKERS", 4), envInt("QUEUE_SIZE", 1000), 2*time.Second)
	defer q.Close()
	q.Register("resize-image", resizeImageJob(root))
	q.Register("send-email", emailJob())

	addr := os.Getenv("ADDR")
	if addr == "" {
		addr = ":8080"
	}
	log.Printf("listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, newServer(q)))
}
