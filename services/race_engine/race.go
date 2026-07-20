// race.go
//
// Implements: Section 7.5 (Race Condition workflow), Section 3
// (race_scanner.py Go-side contract: POST /race {"parallel": N, ...})
// Blueprint: bb_agent_v6.6_final_blueprint.md
//
// WIRE CONTRACT NOTE -- DOCUMENTED ADDITION, NOT A BLUEPRINT CITATION:
// The blueprint specifies the "parallel" field and its default (30,
// Section 3) plus the required evidence outputs (nanosecond timestamps,
// success_count -- Section 7.5) but gives no full request/response JSON
// schema anywhere (confirmed by grep against the source blueprint: only
// `POST /race {"parallel": race_parallel, ...}` appears; no other
// occurrence of 18080, success_count, or nanosecond defines the wire
// shape). The schema below fills that gap, mirroring the resolution
// applied to smuggling_engine's equivalent gap: Go performs only
// transport-level work (fire N concurrent requests, capture nanosecond
// timestamps, classify success via an explicit, caller-supplied
// status-code allowlist); application-semantic interpretation (expected
// vs. vulnerable success count, response-body differential analysis) is
// left to race_scanner.py (Week 7), which is the layer that actually
// knows what a given target's "successful redemption" response looks
// like. UNCONFIRMED against the blueprint -- flagged for review before
// Week 7 race_scanner.py implementation begins (see Week 0 completion
// report).
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	defaultParallel = 30  // Section 3: "Go service fallback default if param absent: 30"
	bodyPreviewCap  = 512 // matches call_target()'s body_preview convention, Section 10.7
)

// raceRequest is the POST /race request body (documented addition, see
// file header).
type raceRequest struct {
	TargetURL          string            `json:"target_url"`
	Method             string            `json:"method"`
	Headers            map[string]string `json:"headers"`
	Body               string            `json:"body"`
	Parallel           int               `json:"parallel"`
	SuccessStatusCodes []int             `json:"success_status_codes"`
}

// raceResult is one concurrent request's outcome.
type raceResult struct {
	Index       int    `json:"index"`
	StatusCode  int    `json:"status_code,omitempty"`
	ElapsedMs   int64  `json:"elapsed_ms"`
	StartNs     int64  `json:"start_ns"`
	BodySHA256  string `json:"body_sha256,omitempty"`
	BodyPreview string `json:"body_preview,omitempty"`
	Error       string `json:"error,omitempty"`
}

// raceResponse is the POST /race response body.
type raceResponse struct {
	Results      []raceResult `json:"results"`
	SuccessCount int          `json:"success_count"`
	Total        int          `json:"total"`
}

type raceServer struct {
	guard  *ScopeGuard
	client *http.Client
	mux    *http.ServeMux
}

func newRaceServer(guard *ScopeGuard) *raceServer {
	s := &raceServer{
		guard:  guard,
		client: &http.Client{Timeout: 15 * time.Second},
		mux:    http.NewServeMux(),
	}
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/race", s.handleRace)
	return s
}

func (s *raceServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

// handleHealth backs the "health_check" requirement in Section 8.5:
// "/health must return 200 within 5s of startup before testing phase
// begins." This handler is effectively instantaneous.
func (s *raceServer) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}

func (s *raceServer) handleRace(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req raceRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.TargetURL == "" {
		http.Error(w, "target_url is required", http.StatusBadRequest)
		return
	}
	// Section 10.2 / 10.8: scope enforced independently in the Go
	// service, before any outbound request is made.
	if !s.guard.IsAllowed(req.TargetURL) {
		http.Error(w, "target_url out of scope", http.StatusForbidden)
		return
	}
	if req.Method == "" {
		req.Method = http.MethodPost
	}
	parallel := req.Parallel
	if parallel <= 0 {
		parallel = defaultParallel // Section 3 default
	}
	successCodes := req.SuccessStatusCodes
	if len(successCodes) == 0 {
		successCodes = []int{http.StatusOK}
	}
	successSet := make(map[int]bool, len(successCodes))
	for _, c := range successCodes {
		successSet[c] = true
	}

	results := make([]raceResult, parallel)
	var wg sync.WaitGroup
	var startBarrier sync.WaitGroup
	startBarrier.Add(1)

	for i := 0; i < parallel; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			startBarrier.Wait() // release all goroutines as close to simultaneously as possible
			results[idx] = s.fireOne(idx, req)
		}(i)
	}
	startBarrier.Done() // release the barrier -> all N goroutines fire together
	wg.Wait()

	successCount := 0
	for _, res := range results {
		if res.Error == "" && successSet[res.StatusCode] {
			successCount++
		}
	}

	resp := raceResponse{Results: results, SuccessCount: successCount, Total: parallel}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

// fireOne sends a single copy of the caller-supplied request and captures
// nanosecond-precision timing (Section 7.5: "nanosecond-precision
// timestamps from Go service").
func (s *raceServer) fireOne(idx int, req raceRequest) raceResult {
	startNs := time.Now().UnixNano()
	start := time.Now()

	var bodyReader io.Reader
	if req.Body != "" {
		bodyReader = strings.NewReader(req.Body)
	}
	httpReq, err := http.NewRequest(req.Method, req.TargetURL, bodyReader)
	if err != nil {
		return raceResult{Index: idx, StartNs: startNs, Error: err.Error()}
	}
	for k, v := range req.Headers {
		httpReq.Header.Set(k, v)
	}

	resp, err := s.client.Do(httpReq)
	elapsed := time.Since(start)
	if err != nil {
		return raceResult{Index: idx, StartNs: startNs, ElapsedMs: elapsed.Milliseconds(), Error: err.Error()}
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20)) // cap read at 1 MiB
	sum := sha256.Sum256(bodyBytes)
	preview := string(bodyBytes)
	if len(preview) > bodyPreviewCap {
		preview = preview[:bodyPreviewCap]
	}

	return raceResult{
		Index:       idx,
		StatusCode:  resp.StatusCode,
		ElapsedMs:   elapsed.Milliseconds(),
		StartNs:     startNs,
		BodySHA256:  hex.EncodeToString(sum[:]),
		BodyPreview: preview,
	}
}
