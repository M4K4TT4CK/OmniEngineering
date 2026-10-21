package main

import (
	"net/http"
	"time"
)

var client = &http.Client{Timeout: 5 * time.Second}

func fetch(url string) (*http.Response, error) {
	return client.Get(url)
}
