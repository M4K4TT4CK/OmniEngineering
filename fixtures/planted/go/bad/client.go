package main

import "net/http"

func fetch(url string) (*http.Response, error) {
	return http.Get(url) // expect: arbiter/go.http-default-client
}
