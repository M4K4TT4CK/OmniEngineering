package main

import (
	"context"
	"io"
	"net/http"
	"time"
)

var client = &http.Client{Timeout: 5 * time.Second}

// Client talks to the upstream pricing service.
type Client struct {
	base string
	http *http.Client
}

func NewClient(base string) *Client {
	return &Client{base: base, http: &http.Client{Timeout: 10 * time.Second}}
}

func fetch(url string) (*http.Response, error) {
	return client.Get(url)
}

func (c *Client) Price(ctx context.Context, sku string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/price/"+sku, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

func newUpstream() http.Client {
	return http.Client{
		Transport: http.DefaultTransport,
		Timeout:   15 * time.Second,
	}
}

// newLazy configures the timeout after construction.
func newLazy(d time.Duration) *http.Client {
	c := &http.Client{}
	c.Timeout = d
	return c
}
