// scope_guard.go
//
// Implements: Section 10.8 -- Go Services Scope Contract
// Blueprint: bb_agent_v6.6_final_blueprint.md
//
// CRITICAL: This file MUST be byte-identical to the copy in the sibling
// service directory (race_engine/ <-> smuggling_engine/). CI enforces
// this via `make ci-scope-diff` (Section 8.5, services/Makefile). Do not
// edit one copy without copying the change verbatim to the other.
//
// Scope is enforced independently at four layers (Section 10.2): the
// Python HTTP client, this Go service, the Playwright entry point, and
// the sandbox call_target(). This file is the Go-service layer -- it has
// no dependency on and shares no runtime state with the Python-side
// scope_enforcer.py, by design (Section 10.2's "no single bypass point").
package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
)

// ScopeGuard enforces the authorized target scope inside this service.
type ScopeGuard struct {
	AllowedPatterns []string
}

// IsAllowed reports whether targetURL's host matches an entry in
// AllowedPatterns. Patterns prefixed "*." are wildcard-aware:
// "*.example.com" matches both "example.com" and any subdomain of it
// (Section 10.8). An unparsable targetURL is never allowed.
func (sg *ScopeGuard) IsAllowed(targetURL string) bool {
	u, err := url.Parse(targetURL)
	if err != nil {
		return false
	}
	host := u.Hostname()
	for _, pattern := range sg.AllowedPatterns {
		if strings.HasPrefix(pattern, "*.") {
			base := strings.TrimPrefix(pattern, "*.")
			suffix := "." + base
			if host == base || strings.HasSuffix(host, suffix) {
				return true
			}
		} else if host == pattern {
			return true
		}
	}
	return false
}

// scopeAllowedDoc mirrors the JSON shape written by
// core/governance/scope_config_generator.py.
type scopeAllowedDoc struct {
	AllowedPatterns []string `json:"allowed_patterns"`
}

// LoadScopeGuard reads a scope_allowed.json file (produced by
// core/governance/scope_config_generator.py from configs/scope.yaml,
// Section 3) and returns a ScopeGuard seeded with its allowed_patterns.
func LoadScopeGuard(path string) (*ScopeGuard, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading scope json %q: %w", path, err)
	}
	var doc scopeAllowedDoc
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("parsing scope json %q: %w", path, err)
	}
	if len(doc.AllowedPatterns) == 0 {
		return nil, fmt.Errorf("scope json %q has an empty allowed_patterns list", path)
	}
	return &ScopeGuard{AllowedPatterns: doc.AllowedPatterns}, nil
}
