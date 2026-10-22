package cwe25

import (
	"net/http"
	"strconv"
)

// PageSize is CWE-190: a 64-bit parse narrowed to int32 wraps (4294967297 becomes 1,
// 2147483648 becomes negative) and the range check below sees the wrapped value.
func PageSize(r *http.Request) int32 {
	n, err := strconv.Atoi(r.URL.Query().Get("limit"))
	if err != nil {
		return 50
	}
	size := int32(n) // gap: CWE-190 - nothing in this build reports it (docs/cwe-top25.md)
	if size > 500 {
		return 500
	}
	return size
}
