// race_test.go
//
// Implements: Section 7.5 test coverage (Week 0 scaffolding -- full
// race_scanner.py integration arrives Week 7). Covers the proposed
// /race wire contract (see race.go header for the flagged-gap note).
// Blueprint: bb_agent_v6.6_final_blueprint.md
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func TestHandleRace_OutOfScopeRejected(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"*.example.com"}}
	srv := newRaceServer(guard)

	body := `{"target_url":"https://evil.com/x","parallel":3}`
	req := httptest.NewRequest(http.MethodPost, "/race", strings.NewReader(body))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for out-of-scope target, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleRace_MissingTargetURLRejected(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	srv := newRaceServer(guard)

	req := httptest.NewRequest(http.MethodPost, "/race", strings.NewReader(`{"parallel":3}`))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing target_url, got %d", w.Code)
	}
}

func TestHandleRace_WrongMethodRejected(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	srv := newRaceServer(guard)

	req := httptest.NewRequest(http.MethodGet, "/race", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestHandleHealth_ReturnsOK(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	srv := newRaceServer(guard)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestHandleRace_ConcurrentFireAndSuccessCount(t *testing.T) {
	// Local test target standing in for the real bug bounty target --
	// this sandbox has no egress to arbitrary internet hosts, so every
	// integration-style test in this file uses httptest.NewServer bound
	// to 127.0.0.1.
	var mu sync.Mutex
	hits := 0
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hits++
		n := hits
		mu.Unlock()
		if n <= 2 {
			w.WriteHeader(http.StatusOK)
		} else {
			w.WriteHeader(http.StatusConflict)
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer target.Close()

	host := strings.Split(strings.TrimPrefix(target.URL, "http://"), ":")[0]
	guard := &ScopeGuard{AllowedPatterns: []string{host}}
	srv := newRaceServer(guard)

	reqBody, _ := json.Marshal(raceRequest{
		TargetURL: target.URL,
		Method:    "POST",
		Parallel:  5,
	})
	req := httptest.NewRequest(http.MethodPost, "/race", bytes.NewReader(reqBody))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp raceResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("bad JSON response: %v", err)
	}
	if resp.Total != 5 {
		t.Fatalf("expected total=5, got %d", resp.Total)
	}
	if resp.SuccessCount != 2 {
		t.Fatalf("expected success_count=2 (exactly 2 of 5 requests won the 200 race), got %d: %+v", resp.SuccessCount, resp.Results)
	}
	if len(resp.Results) != 5 {
		t.Fatalf("expected 5 results, got %d", len(resp.Results))
	}
	for _, r := range resp.Results {
		if r.StartNs == 0 {
			t.Errorf("result %d missing start_ns (Section 7.5 requires nanosecond timestamps)", r.Index)
		}
	}
}

func TestHandleRace_DefaultParallelWhenAbsent(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	host := strings.Split(strings.TrimPrefix(target.URL, "http://"), ":")[0]
	guard := &ScopeGuard{AllowedPatterns: []string{host}}
	srv := newRaceServer(guard)

	reqBody, _ := json.Marshal(raceRequest{TargetURL: target.URL}) // no "parallel" field
	req := httptest.NewRequest(http.MethodPost, "/race", bytes.NewReader(reqBody))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	var resp raceResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("bad JSON: %v", err)
	}
	if resp.Total != defaultParallel {
		t.Fatalf("expected default parallel=%d (Section 3), got %d", defaultParallel, resp.Total)
	}
}

func TestHandleRace_CustomSuccessStatusCodes(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated) // 201, not the default-success 200
	}))
	defer target.Close()
	host := strings.Split(strings.TrimPrefix(target.URL, "http://"), ":")[0]
	guard := &ScopeGuard{AllowedPatterns: []string{host}}
	srv := newRaceServer(guard)

	reqBody, _ := json.Marshal(raceRequest{
		TargetURL:          target.URL,
		Parallel:           4,
		SuccessStatusCodes: []int{201},
	})
	req := httptest.NewRequest(http.MethodPost, "/race", bytes.NewReader(reqBody))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	var resp raceResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("bad JSON: %v", err)
	}
	if resp.SuccessCount != 4 {
		t.Fatalf("expected all 4 to count as success under success_status_codes=[201], got %d", resp.SuccessCount)
	}
}
