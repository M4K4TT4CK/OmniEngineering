package main

import (
	"crypto/tls"
	"net/http"
	"time"
)

// upstreamTransport talks to the internal pricing service over TLS.
func upstreamTransport() *http.Client {
	return &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
		},
	}
}
