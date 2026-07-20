// smuggling.go
//
// Implements: Section 7.21 (HTTP Smuggling workflow), Section 3
// (smuggling_engine Go-side contract)
// Blueprint: bb_agent_v6.6_final_blueprint.md
//
// WIRE CONTRACT: POST /smuggle -- resolved as a documented addition to a
// genuine blueprint gap (confirmed absent via grep: 18081, /smuggle,
// CL.TE, TE.CL, SetReadDeadline all appear only in evidence-type and
// detection-logic prose, never as a full wire schema). Contract:
//
//	POST /smuggle
//	{"target_url": "...", "variants": [
//	    {"type": "CL.TE", "raw_request_b64": "..."},
//	    {"type": "TE.CL", "raw_request_b64": "..."}
//	]}
//	-> {"results": [{"type": "CL.TE", "timed_out": bool, "elapsed_ms": n}, ...],
//	    "vulnerable": bool}
//
// raw_request_b64 is base64-encoded because raw HTTP/1.1 smuggling probes
// contain byte sequences (duplicate/conflicting Content-Length and
// Transfer-Encoding headers, ambiguous chunk framing) that do not survive
// JSON string escaping cleanly. Python (payload_engine.py, an
// injectable_payload consumer per Section 3.1) builds the raw bytes from
// smuggling_configs.json / http_smuggling_payloads.json; Go only performs
// raw socket I/O against the already-built bytes, mirroring race_engine's
// division of labor.
package main

import (
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"time"
)

// readDeadline implements Section 7.21: "SetReadDeadline(10s)". It is a
// package var (not const) so tests can shorten it instead of waiting out
// a real 10-second timeout.
var readDeadline = 10 * time.Second

type smuggleVariant struct {
	Type          string `json:"type"`
	RawRequestB64 string `json:"raw_request_b64"`
}

type smuggleRequest struct {
	TargetURL string           `json:"target_url"`
	Variants  []smuggleVariant `json:"variants"`
}

type smuggleResult struct {
	Type      string `json:"type"`
	TimedOut  bool   `json:"timed_out"`
	ElapsedMs int64  `json:"elapsed_ms"`
	Error     string `json:"error,omitempty"`
}

type smuggleResponse struct {
	Results    []smuggleResult `json:"results"`
	Vulnerable bool            `json:"vulnerable"`
}

type smugglingServer struct {
	guard *ScopeGuard
	mux   *http.ServeMux
}

func newSmugglingServer(guard *ScopeGuard) *smugglingServer {
	s := &smugglingServer{guard: guard, mux: http.NewServeMux()}
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/smuggle", s.handleSmuggle)
	return s
}

func (s *smugglingServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

// handleHealth backs Section 8.5's "/health must return 200 within 5s of
// startup before testing phase begins."
func (s *smugglingServer) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}

func (s *smugglingServer) handleSmuggle(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req smuggleRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.TargetURL == "" {
		http.Error(w, "target_url is required", http.StatusBadRequest)
		return
	}
	// Section 10.2 / 10.8: scope enforced independently in the Go
	// service, before any outbound connection is opened.
	if !s.guard.IsAllowed(req.TargetURL) {
		http.Error(w, "target_url out of scope", http.StatusForbidden)
		return
	}
	if len(req.Variants) == 0 {
		http.Error(w, "variants must be non-empty", http.StatusBadRequest)
		return
	}

	hostPort, useTLS, err := resolveHostPort(req.TargetURL)
	if err != nil {
		http.Error(w, "bad target_url: "+err.Error(), http.StatusBadRequest)
		return
	}

	// Section 7.21: "probes CL.TE and TE.CL" -- both variants are always
	// sent (not configurable like race_parallel is), sequentially: each
	// probe is testing individual connection/request-parsing behavior on
	// its own fresh connection, not concurrency, so there is no reason to
	// fire them in parallel the way race_engine fires its N copies.
	results := make([]smuggleResult, 0, len(req.Variants))
	for _, v := range req.Variants {
		results = append(results, probeVariant(hostPort, useTLS, v))
	}

	vulnerable := false
	if len(results) == 2 {
		// The timing gap between the two variants is the differential
		// signal (Section 7.21: "timing_anomaly + differential = 2");
		// that gap is only meaningful when they DIFFER. Both timing out,
		// or neither, is inconclusive, not a positive signal.
		vulnerable = results[0].TimedOut != results[1].TimedOut
	}

	resp := smuggleResponse{Results: results, Vulnerable: vulnerable}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

func resolveHostPort(targetURL string) (hostPort string, useTLS bool, err error) {
	u, err := url.Parse(targetURL)
	if err != nil {
		return "", false, err
	}
	useTLS = u.Scheme == "https"
	host := u.Hostname()
	if host == "" {
		return "", false, fmt.Errorf("no host in target_url")
	}
	port := u.Port()
	if port == "" {
		if useTLS {
			port = "443"
		} else {
			port = "80"
		}
	}
	return net.JoinHostPort(host, port), useTLS, nil
}

// probeVariant opens a fresh raw connection, writes the decoded raw
// request bytes, and measures whether a read completes before
// readDeadline (Section 7.21: SetReadDeadline(10s)).
func probeVariant(hostPort string, useTLS bool, v smuggleVariant) smuggleResult {
	raw, err := base64.StdEncoding.DecodeString(v.RawRequestB64)
	if err != nil {
		return smuggleResult{Type: v.Type, Error: fmt.Sprintf("bad base64: %v", err)}
	}

	dialer := net.Dialer{Timeout: 10 * time.Second}
	var conn net.Conn
	if useTLS {
		h, _, splitErr := net.SplitHostPort(hostPort)
		if splitErr != nil {
			h = hostPort
		}
		conn, err = tls.DialWithDialer(&dialer, "tcp", hostPort, &tls.Config{ServerName: h})
	} else {
		conn, err = dialer.Dial("tcp", hostPort)
	}
	if err != nil {
		return smuggleResult{Type: v.Type, Error: fmt.Sprintf("dial: %v", err)}
	}
	defer conn.Close()

	if _, err := conn.Write(raw); err != nil {
		return smuggleResult{Type: v.Type, Error: fmt.Sprintf("write: %v", err)}
	}

	start := time.Now()
	_ = conn.SetReadDeadline(start.Add(readDeadline)) // Section 7.21
	buf := make([]byte, 4096)
	_, readErr := conn.Read(buf)
	elapsed := time.Since(start)

	timedOut := false
	if netErr, ok := readErr.(net.Error); ok && netErr.Timeout() {
		timedOut = true
	}

	return smuggleResult{
		Type:      v.Type,
		TimedOut:  timedOut,
		ElapsedMs: elapsed.Milliseconds(),
	}
}
