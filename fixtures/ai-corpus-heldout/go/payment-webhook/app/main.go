package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	secret := os.Getenv("WEBHOOK_SECRET")
	if secret == "" {
		log.Fatal("WEBHOOK_SECRET must be set")
	}
	dbPath := getenv("DB_PATH", "payments.db")
	addr := getenv("ADDR", ":8080")

	svc, err := Open(dbPath, []byte(secret))
	if err != nil {
		log.Fatal(err)
	}
	defer svc.Close()

	log.Printf("listening on %s, database %s", addr, dbPath)
	log.Fatal(http.ListenAndServe(addr, svc.Routes()))
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
