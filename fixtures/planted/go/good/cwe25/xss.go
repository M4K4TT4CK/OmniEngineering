// Package cwe25 holds the CWE Top 25 (2024) planted pairs for Go.
package cwe25

import (
	"html/template"
	"net/http"
)

var greetPage = template.Must(template.New("greet").Parse("<h1>Hello, {{.}}!</h1>"))

// Greet renders the name through html/template, which escapes it.
func Greet(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = greetPage.Execute(w, r.URL.Query().Get("name"))
}
