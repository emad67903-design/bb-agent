// main.go
//
// Implements: Section 3 (services/race_engine/main.go), Section 8.5 (Go
// Services Process Management), Section 10.8 (Go Services Scope Contract)
// Blueprint: bb_agent_v6.6_final_blueprint.md
//
// race_engine is a standalone Go service that fires N concurrent copies
// of a caller-supplied HTTP request at an in-scope target and reports
// per-request timing and outcome, for race_scanner.py's use (Week 7).
// Binds 127.0.0.1 only -- never 0.0.0.0 (Section 8.5).
package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	scopeJSONPath := flag.String("scope-json", "", "path to scope_allowed.json (required)")
	port := flag.String("port", "18080", "port to bind on 127.0.0.1 (Section 8.5)")
	flag.Parse()

	if *scopeJSONPath == "" {
		log.Fatal("race_engine: -scope-json is required (Section 3)")
	}

	guard, err := LoadScopeGuard(*scopeJSONPath)
	if err != nil {
		log.Fatalf("race_engine: failed to load scope guard: %v", err)
	}
	log.Printf("race_engine: loaded %d scope pattern(s) from %s", len(guard.AllowedPatterns), *scopeJSONPath)

	srv := newRaceServer(guard)

	// Section 8.5: "Binding: 127.0.0.1 only -- never 0.0.0.0."
	addr := "127.0.0.1:" + *port
	httpServer := &http.Server{
		Addr:    addr,
		Handler: srv,
	}

	serveErrCh := make(chan error, 1)
	go func() {
		log.Printf("race_engine: listening on %s", addr)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			serveErrCh <- err
		}
	}()

	// Section 8.5: SIGTERM triggers graceful shutdown; process_supervisor.py
	// (Python, Week 6+) sends SIGKILL after a 10s grace period if this
	// process has not exited by then.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, os.Interrupt)

	select {
	case err := <-serveErrCh:
		log.Fatalf("race_engine: ListenAndServe: %v", err)
	case <-sigCh:
		log.Println("race_engine: shutdown signal received, draining (10s grace)")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(ctx); err != nil {
		log.Printf("race_engine: forced shutdown: %v", err)
	}
}
