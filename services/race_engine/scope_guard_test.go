// scope_guard_test.go
//
// Implements: Section 10.8 test coverage -- Go Services Scope Contract
// Blueprint: bb_agent_v6.6_final_blueprint.md
package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIsAllowed_ExactMatch(t *testing.T) {
	sg := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	if !sg.IsAllowed("https://example.com/path") {
		t.Fatal("expected exact host match to be allowed")
	}
}

func TestIsAllowed_WildcardMatchesBaseAndSubdomain(t *testing.T) {
	sg := &ScopeGuard{AllowedPatterns: []string{"*.example.com"}}
	cases := []struct {
		url  string
		want bool
	}{
		{"https://example.com/", true},         // base domain itself
		{"https://sub.example.com/", true},      // subdomain
		{"https://deep.sub.example.com/", true}, // nested subdomain
		{"https://evilexample.com/", false},     // NOT a suffix match, must be dot-bounded
		{"https://notexample.com/", false},
		{"https://example.com.evil.com/", false}, // host must END with the suffix
	}
	for _, c := range cases {
		got := sg.IsAllowed(c.url)
		if got != c.want {
			t.Errorf("IsAllowed(%q) = %v, want %v", c.url, got, c.want)
		}
	}
}

func TestIsAllowed_NoMatch(t *testing.T) {
	sg := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	if sg.IsAllowed("https://attacker.com/") {
		t.Fatal("expected out-of-scope host to be rejected")
	}
}

func TestIsAllowed_InvalidURLRejected(t *testing.T) {
	sg := &ScopeGuard{AllowedPatterns: []string{"example.com"}}
	if sg.IsAllowed("::not a url::") {
		t.Fatal("expected invalid URL to be rejected, not allowed")
	}
}

func TestIsAllowed_EmptyPatternListRejectsEverything(t *testing.T) {
	sg := &ScopeGuard{AllowedPatterns: []string{}}
	if sg.IsAllowed("https://example.com/") {
		t.Fatal("expected empty pattern list to allow nothing")
	}
}

func TestLoadScopeGuard_Success(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "scope_allowed.json")
	content := `{"allowed_patterns":["*.example.com","test.local"],"generated_at":"2026-07-20T00:00:00Z"}`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	guard, err := LoadScopeGuard(path)
	if err != nil {
		t.Fatalf("LoadScopeGuard: %v", err)
	}
	if len(guard.AllowedPatterns) != 2 {
		t.Fatalf("expected 2 patterns, got %d: %v", len(guard.AllowedPatterns), guard.AllowedPatterns)
	}
	if !guard.IsAllowed("https://sub.example.com/") {
		t.Error("expected loaded guard to allow sub.example.com")
	}
}

func TestLoadScopeGuard_MissingFile(t *testing.T) {
	if _, err := LoadScopeGuard("/nonexistent/scope_allowed.json"); err == nil {
		t.Fatal("expected error for missing file")
	}
}

func TestLoadScopeGuard_InvalidJSON(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "scope_allowed.json")
	if err := os.WriteFile(path, []byte("{not valid json"), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	if _, err := LoadScopeGuard(path); err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

func TestLoadScopeGuard_EmptyPatternsRejected(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "scope_allowed.json")
	if err := os.WriteFile(path, []byte(`{"allowed_patterns":[]}`), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	if _, err := LoadScopeGuard(path); err == nil {
		t.Fatal("expected error for empty allowed_patterns")
	}
}
