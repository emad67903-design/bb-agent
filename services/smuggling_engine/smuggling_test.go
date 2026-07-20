// smuggling_test.go
//
// Implements: Section 7.21 test coverage -- HTTP Smuggling workflow
// Blueprint: bb_agent_v6.6_final_blueprint.md
package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestHandleSmuggle_OutOfScopeRejected(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"*.example.com"}}
	srv := newSmugglingServer(guard)

	body := `{"target_url":"https://evil.com/","variants":[{"type":"CL.TE","raw_request_b64":"YQ=="}]}`
	req := httptest.NewRequest(http.MethodPost, "/smuggle", strings.NewReader(body))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for out-of-scope target, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleSmuggle_EmptyVariantsRejected(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	srv := newSmugglingServer(guard)

	body := `{"target_url":"https://example.com/","variants":[]}`
	req := httptest.NewRequest(http.MethodPost, "/smuggle", strings.NewReader(body))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for empty variants, got %d", w.Code)
	}
}

func TestHandleHealth_ReturnsOK(t *testing.T) {
	guard := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	srv := newSmugglingServer(guard)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestProbeVariant_BadBase64ReturnsErrorResultNotCrash(t *testing.T) {
	result := probeVariant("127.0.0.1:1", false, smuggleVariant{Type: "CL.TE", RawRequestB64: "not-valid-base64!!"})
	if result.Error == "" {
		t.Fatal("expected an error result for invalid base64, got none")
	}
	if result.TimedOut {
		t.Fatal("bad base64 should not be reported as timed_out")
	}
}

func TestHandleSmuggle_NotVulnerableWhenBothRespondPromptly(t *testing.T) {
	orig := readDeadline
	readDeadline = 300 * time.Millisecond
	defer func() { readDeadline = orig }()

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()

	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				buf := make([]byte, 4096)
				_, _ = c.Read(buf)
				_, _ = c.Write([]byte("HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"))
			}(conn)
		}
	}()

	guard := &ScopeGuard{AllowedPatterns: []string{"127.0.0.1"}}
	srv := newSmugglingServer(guard)

	targetURL := "http://" + ln.Addr().String() + "/"
	reqBody, _ := json.Marshal(smuggleRequest{
		TargetURL: targetURL,
		Variants: []smuggleVariant{
			{Type: "CL.TE", RawRequestB64: base64.StdEncoding.EncodeToString([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n"))},
			{Type: "TE.CL", RawRequestB64: base64.StdEncoding.EncodeToString([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n"))},
		},
	})
	req := httptest.NewRequest(http.MethodPost, "/smuggle", bytes.NewReader(reqBody))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	var resp smuggleResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("bad JSON: %v", err)
	}
	if resp.Vulnerable {
		t.Fatalf("expected vulnerable=false when both variants respond promptly, got results=%+v", resp.Results)
	}
}

func TestHandleSmuggle_VulnerableWhenExactlyOneTimesOut(t *testing.T) {
	orig := readDeadline
	readDeadline = 150 * time.Millisecond
	defer func() { readDeadline = orig }()

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()

	var connCount int32
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			first := atomic.AddInt32(&connCount, 1) == 1
			go func(c net.Conn, respondPromptly bool) {
				defer c.Close()
				buf := make([]byte, 4096)
				_, _ = c.Read(buf) // drain the raw request
				if respondPromptly {
					_, _ = c.Write([]byte("HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"))
					return
				}
				// Simulates the backend hanging, waiting for a body that
				// never arrives under this interpretation of the request
				// (the classic CL.TE/TE.CL smuggling signal). Sleep well
				// past the client's shortened test deadline so the
				// client's own SetReadDeadline is what fires -- not a
				// server-side close.
				time.Sleep(500 * time.Millisecond)
			}(conn, first)
		}
	}()

	guard := &ScopeGuard{AllowedPatterns: []string{"127.0.0.1"}}
	srv := newSmugglingServer(guard)

	targetURL := "http://" + ln.Addr().String() + "/"
	reqBody, _ := json.Marshal(smuggleRequest{
		TargetURL: targetURL,
		Variants: []smuggleVariant{
			{Type: "CL.TE", RawRequestB64: base64.StdEncoding.EncodeToString([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n"))},
			{Type: "TE.CL", RawRequestB64: base64.StdEncoding.EncodeToString([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n"))},
		},
	})
	req := httptest.NewRequest(http.MethodPost, "/smuggle", bytes.NewReader(reqBody))
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp smuggleResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("bad JSON: %v", err)
	}
	if !resp.Vulnerable {
		t.Fatalf("expected vulnerable=true when exactly one variant times out, got results=%+v", resp.Results)
	}
	if resp.Results[0].TimedOut {
		t.Errorf("expected first (promptly-responding) variant timed_out=false, got true")
	}
	if !resp.Results[1].TimedOut {
		t.Errorf("expected second (hanging) variant timed_out=true, got false")
	}
}

func TestResolveHostPort(t *testing.T) {
	cases := []struct {
		url      string
		wantHP   string
		wantTLS  bool
		wantFail bool
	}{
		{"https://example.com/x", "example.com:443", true, false},
		{"http://example.com/x", "example.com:80", false, false},
		{"https://example.com:8443/x", "example.com:8443", true, false},
		{"://bad", "", false, true},
	}
	for _, c := range cases {
		hp, tlsOn, err := resolveHostPort(c.url)
		if c.wantFail {
			if err == nil {
				t.Errorf("resolveHostPort(%q): expected error, got none", c.url)
			}
			continue
		}
		if err != nil {
			t.Errorf("resolveHostPort(%q): unexpected error: %v", c.url, err)
			continue
		}
		if hp != c.wantHP || tlsOn != c.wantTLS {
			t.Errorf("resolveHostPort(%q) = (%q, %v), want (%q, %v)", c.url, hp, tlsOn, c.wantHP, c.wantTLS)
		}
	}
}
