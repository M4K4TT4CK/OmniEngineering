package main

import (
	"context"
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	configPath := flag.String("config", "backup.json", "path to the JSON config file")
	once := flag.Bool("once", false, "run one backup immediately and exit")
	flag.Parse()

	logger := log.New(os.Stderr, "", log.LstdFlags)

	cfg, err := LoadConfig(*configPath)
	if err != nil {
		logger.Fatalf("config: %v", err)
	}
	store, err := NewS3Store(cfg.S3)
	if err != nil {
		logger.Fatalf("s3: %v", err)
	}
	runner := NewRunner(cfg, store, logger)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if *once {
		if st := runner.Run(ctx); !st.OK {
			os.Exit(1)
		}
		return
	}

	mux := http.NewServeMux()
	mux.Handle("/status", statusHandler(runner))
	srv := &http.Server{
		Addr:              cfg.Listen,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatalf("http: %v", err)
		}
	}()

	logger.Printf("running a backup every %s; status at %s/status", cfg.IntervalDuration(), cfg.Listen)
	ticker := time.NewTicker(cfg.IntervalDuration())
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			if err := srv.Shutdown(shutdownCtx); err != nil {
				logger.Printf("http shutdown: %v", err)
			}
			cancel()
			logger.Println("stopped")
			return
		case <-ticker.C:
			runner.Run(ctx)
		}
	}
}

// statusHandler serves the most recent run as JSON. last_run is null before the first run.
func statusHandler(r *Runner) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(struct {
			LastRun *Status `json:"last_run"`
		}{r.Last()})
	})
}
