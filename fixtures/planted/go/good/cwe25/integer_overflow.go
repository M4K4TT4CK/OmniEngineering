package cwe25

import (
	"net/http"
	"strconv"
)

// PageSize parses at the width it stores and bounds both ends.
func PageSize(r *http.Request) int32 {
	n, err := strconv.ParseInt(r.URL.Query().Get("limit"), 10, 32)
	if err != nil || n < 1 {
		return 50
	}
	if n > 500 {
		return 500
	}
	return int32(n)
}
