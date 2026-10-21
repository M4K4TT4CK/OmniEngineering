package main

import (
	"io"
	"net/http"
)

// Client talks to the upstream pricing service.
type Client struct {
	base string
	http *http.Client
}

func NewClient(base string) *Client {
	return &Client{base: base, http: &http.Client{}} // expect: arbiter/go.http-client-no-timeout
}

func fetch(url string) (*http.Response, error) {
	return http.Get(url) // expect: arbiter/go.http-default-client
}

func (c *Client) Price(sku string) ([]byte, error) {
	req, err := http.NewRequest(http.MethodGet, c.base+"/price/"+sku, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req) // expect: arbiter/go.http-default-client
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

func newUpstream() http.Client {
	return http.Client{Transport: http.DefaultTransport} // expect: arbiter/go.http-client-no-timeout
}
