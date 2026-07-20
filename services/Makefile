# services/Makefile
#
# Implements: Section 8.5 -- Go Services Process Management (CI scope diff)
# Blueprint: bb_agent_v6.6_final_blueprint.md

.PHONY: ci-scope-diff build test vet

ci-scope-diff:
	@diff race_engine/scope_guard.go smuggling_engine/scope_guard.go \
		&& echo "OK: scope_guard.go is byte-identical in both services" \
		|| (echo "FAIL: scope_guard.go has DRIFTED between race_engine/ and smuggling_engine/ -- Section 8.5 requires byte-identical" && exit 1)

build:
	cd race_engine && go build ./...
	cd smuggling_engine && go build ./...

vet:
	cd race_engine && go vet ./...
	cd smuggling_engine && go vet ./...

test:
	cd race_engine && go test ./...
	cd smuggling_engine && go test ./...
