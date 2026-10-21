package main

import (
	"context"
	"io"
	"net/http"
)

// API is a library client: the caller's context carries the deadline, so the
// client itself sets no Timeout (the same shape as the Prometheus API client).
type API struct {
	base   string
	client *http.Client
}

func NewAPI(base string, rt http.RoundTripper) *API {
	return &API{base: base, client: &http.Client{Transport: rt}}
}

// Do sends req with ctx attached; a ctx with a deadline bounds the call.
func (a *API) Do(ctx context.Context, req *http.Request) ([]byte, error) {
	req = req.WithContext(ctx)
	resp, err := a.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}
