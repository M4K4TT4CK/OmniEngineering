package main

import (
	"log"
	"net/http"
	"time"
)

// main wires the pool up and serves until the process is stopped. The
// progress goroutine lives exactly as long as the process, so it needs no
// stop signal of its own.
func main() {
	p := NewPool(4)
	go func() {
		for {
			log.Printf("processed=%d", p.processed.Load())
			time.Sleep(30 * time.Second)
		}
	}()
	if err := http.ListenAndServe(":8080", nil); err != nil {
		log.Fatal(err)
	}
}
