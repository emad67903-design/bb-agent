# Makefile
#
# Implements: Section 3's base_scanner.py CI hook ("CI pre-commit hook:
# grep -r "^import httpx\|^from httpx\|^import requests" core/scanners/
# -> must return empty"). Also implements docs/DECISIONS.md item 22
# ("CI pre-commit hook enforces no direct httpx/requests imports in
# core/scanners/").
# Blueprint: bb_agent_v6.6_final_blueprint.md
#
# Mirrors services/Makefile's existing ci-scope-diff pattern (Week 0) --
# same OK/FAIL echo style, same "exit 1 on failure" contract -- rather
# than inventing a different CI-check convention for the Python side.

.PHONY: ci-scanner-http-check

ci-scanner-http-check:
	@if grep -rn "^import httpx\|^from httpx\|^import requests" core/scanners/; then \
		echo "FAIL: direct httpx/requests import found in core/scanners/ -- all scanner HTTP calls MUST use self.session (RateLimitedClient). Section 3's base_scanner.py contract."; \
		exit 1; \
	else \
		echo "OK: no direct httpx/requests imports in core/scanners/"; \
	fi
