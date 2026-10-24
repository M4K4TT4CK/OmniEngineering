package main

import (
	"flag"
	"log"
	"net/http"
	"time"
)

func main() {
	addr := flag.String("addr", ":8080", "listen address")
	cacheDir := flag.String("cache-dir", "./cache", "directory for cached thumbnails")
	baseURL := flag.String("base-url", "http://localhost:8080", "public base URL used in returned thumbnail URLs")
	maxUploadMB := flag.Int64("max-upload-mb", 20, "maximum source image size in MiB")
	flag.Parse()

	maxBytes := *maxUploadMB << 20
	srv, err := NewServer(Config{
		CacheDir:       *cacheDir,
		BaseURL:        *baseURL,
		MaxUploadBytes: maxBytes,
		Fetcher:        NewFetcher(maxBytes, false),
	})
	if err != nil {
		log.Fatal(err)
	}

	hs := &http.Server{
		Addr:              *addr,
		Handler:           srv,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		WriteTimeout:      120 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	log.Printf("listening on %s, caching in %s", *addr, *cacheDir)
	log.Fatal(hs.ListenAndServe())
}
