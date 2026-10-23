// Package dataflow: the same shapes as the planted defects, done safely.
package dataflow

import (
	"database/sql"
	"encoding/json"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const shell = "sh"

var client = &http.Client{Timeout: 5 * time.Second}

type previewReq struct {
	URL string `json:"url"`
}

func ownerQuery() string {
	return "SELECT id FROM notes WHERE owner = $1"
}

// Notes runs a constant query with a bound parameter.
func Notes(db *sql.DB, owner string) (*sql.Rows, error) {
	return db.Query(ownerQuery(), owner)
}

// Archive runs a script that is constant once the dead branch is gone.
func Archive(dir string) error {
	script := "tar czf /exports/out.tgz /data"
	if false {
		script = "tar czf /exports/out.tgz " + dir
	}
	return exec.Command(shell, "-c", script).Run()
}

// Preview fetches a fixed host, with the request value escaped into the path.
func Preview(rw http.ResponseWriter, req *http.Request) {
	var body previewReq
	if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
		http.Error(rw, "bad request", http.StatusBadRequest)
		return
	}
	target := "https://previews.example.com/render/" + url.PathEscape(body.URL)
	resp, err := client.Get(target)
	if err != nil {
		log.Printf("preview: %v", err)
		msg := "upstream failed"
		http.Error(rw, msg, http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	rw.WriteHeader(http.StatusOK)
}

// Download keeps only the base name of the requested file.
func Download(out http.ResponseWriter, in *http.Request) {
	q := in.URL.Query()
	name := filepath.Base(q.Get("file"))
	f, err := os.Open(filepath.Join("/srv/reports", name))
	if err != nil {
		log.Printf("open: %v", err)
		http.Error(out, "not found", http.StatusNotFound)
		return
	}
	defer f.Close()
}
