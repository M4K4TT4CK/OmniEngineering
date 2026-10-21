// Package cwe25 holds the CWE Top 25 (2024) planted pairs for Go.
package cwe25

import (
	"fmt"
	"net/http"
)

// Greet is CWE-79: the name goes into HTML unescaped.
func Greet(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprintf(w, "<h1>Hello, %s!</h1>", r.URL.Query().Get("name")) // expect-ext: semgrep/semgrep-rules.go.lang.security.audit.xss.no-fprintf-to-responsewriter, semgrep/semgrep-rules.go.lang.security.injection.raw-html-format
}
