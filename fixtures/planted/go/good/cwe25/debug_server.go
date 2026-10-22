package cwe25

import (
	"net/http"
	_ "net/http/pprof"
	"time"
)

// DebugServer serves the default mux, pprof included, on the loopback interface only.
func DebugServer() error {
	srv := &http.Server{Addr: "localhost:6060", ReadHeaderTimeout: 5 * time.Second}
	return srv.ListenAndServe()
}
