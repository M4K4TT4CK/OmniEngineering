package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	cfg := Config{
		ClientID:     os.Getenv("GITHUB_CLIENT_ID"),
		ClientSecret: os.Getenv("GITHUB_CLIENT_SECRET"),
		BaseURL:      getenv("BASE_URL", "http://localhost:8080"),
	}
	if cfg.ClientID == "" || cfg.ClientSecret == "" {
		log.Fatal("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set")
	}

	db, err := OpenDB(getenv("DATABASE_PATH", "app.db"))
	if err != nil {
		log.Fatalf("open database: %v", err)
	}
	defer db.Close()

	app, err := NewApp(cfg, db)
	if err != nil {
		log.Fatalf("configure app: %v", err)
	}

	srv := &http.Server{
		Addr:              getenv("ADDR", ":8080"),
		Handler:           app.Routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		log.Printf("listening on %s (public URL %s)", srv.Addr, cfg.BaseURL)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("server: %v", err)
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("shutdown: %v", err)
	}
}
