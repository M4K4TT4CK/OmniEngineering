// Package dataflow plants defects one step from their sink (Milestone 3):
// through a local, a decoded body or a same-file helper.
package dataflow

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
)

const shell = "sh"

type previewReq struct {
	URL string `json:"url"`
}

func ownerQuery(owner string) string {
	return "SELECT id FROM notes WHERE owner = '" + owner + "'"
}

// Notes runs SQL that a helper built.
func Notes(db *sql.DB, owner string) (*sql.Rows, error) {
	return db.Query(ownerQuery(owner)) // expect: arbiter/go.sql-injection
}

// Archive runs a shell whose name was hoisted into a constant.
func Archive(dir string) error {
	script := "tar czf /exports/out.tgz " + dir
	return exec.Command(shell, "-c", script).Run() // expect: arbiter/go.command-injection
}

// Preview fetches a URL decoded from the request body.
func Preview(rw http.ResponseWriter, req *http.Request) {
	var body previewReq
	if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
		http.Error(rw, "bad request", http.StatusBadRequest)
		return
	}
	target := body.URL
	resp, err := http.Get(target) // expect: arbiter/go.ssrf-request-url, arbiter/go.http-default-client
	if err != nil {
		msg := err.Error()
		http.Error(rw, msg, http.StatusBadGateway) // expect: arbiter/go.error-text-in-response
		return
	}
	defer resp.Body.Close()
	rw.WriteHeader(http.StatusOK)
}

// Download opens a file named by the query string, through a local.
func Download(out http.ResponseWriter, in *http.Request) {
	q := in.URL.Query()
	name := q.Get("file")
	f, err := os.Open(filepath.Join("/srv/reports", name)) // expect: arbiter/go.path-traversal-request
	if err != nil {
		fmt.Fprintf(out, "open failed: %v", err) // expect: arbiter/go.error-text-in-response
		return
	}
	defer f.Close()
}
