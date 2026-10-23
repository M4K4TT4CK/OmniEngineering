package aicode

import (
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"sync"
)

type job struct {
	ID   string
	Done bool
}

type jobQueue struct {
	mu   sync.Mutex
	jobs map[string]*job
}

func (q *jobQueue) submit() (*job, error) {
	var b [12]byte
	if _, err := rand.Read(b[:]); err != nil {
		return nil, err
	}
	j := &job{ID: hex.EncodeToString(b[:])}
	q.mu.Lock()
	q.jobs[j.ID] = j
	q.mu.Unlock()
	return j, nil
}

// forget drops finished jobs; a ticker calls it.
func (q *jobQueue) forget() {
	q.mu.Lock()
	defer q.mu.Unlock()
	for id, j := range q.jobs {
		if j.Done {
			delete(q.jobs, id)
		}
	}
}

type api struct{}

func (a *api) authed(h http.HandlerFunc) http.HandlerFunc { return h }
func (a *api) list(w http.ResponseWriter, r *http.Request)   {}
func (a *api) remove(w http.ResponseWriter, r *http.Request) {}

func (a *api) routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /notes", a.authed(a.list))
	mux.HandleFunc("DELETE /notes/{id}", a.authed(a.remove))
}
