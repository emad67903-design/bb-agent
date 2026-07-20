# services/ — Go Microservices

Implements: Section 3 (`services/README.md`), Section 8.5 (Go Services
Process Management), Section 10.8 (Go Services Scope Contract)
Blueprint: bb_agent_v6.6_final_blueprint.md

Two independent, stdlib-only Go binaries. Neither imports the other or
shares runtime state — the only thing they share is source text
(`scope_guard.go`, enforced byte-identical by CI), by design (Section
10.2: scope enforcement has "no single bypass point").

| Service | Port | Purpose | Blueprint |
|---|---|---|---|
| `race_engine` | 18080 | Fires N concurrent copies of a caller-supplied HTTP request; reports per-request nanosecond timing and status | Section 7.5 |
| `smuggling_engine` | 18081 | Probes CL.TE / TE.CL request-smuggling variants over raw sockets with a 10s read deadline | Section 7.21 |

## Wire contracts

Both services' request/response JSON schemas are **documented additions**,
not blueprint citations — the blueprint specifies evidence requirements
and (for race) the `parallel` field, but no full wire schema for either
service (verified by grep against the source blueprint; see Week 0
completion report for the full resolution record). See the header
comments in `race_engine/race.go` and `smuggling_engine/smuggling.go` for
the exact schemas and their justification. Both are flagged for review
before their respective Week 7 Python callers (`race_scanner.py`,
`http_smuggling.py`) are implemented.

## Startup

```
race_engine    -scope-json <path> [-port 18080]
smuggling_engine  -scope-json <path> [-port 18081]
```

`-scope-json` is **required** in both — points at
`services/scope_allowed.json`, generated from `configs/scope.yaml` by
`core/governance/scope_config_generator.py` (the `scope_json_gen`
preflight check). Startup fails fast (`log.Fatal`) if the flag is absent,
the file is missing, or `allowed_patterns` is empty.

- **Process owner:** `process_supervisor.py` (Week 6+) starts both
  services at "testing" phase entry (Section 9.2: Phases 5–8 share the
  `testing` resource profile).
- **Binding:** `127.0.0.1` only, hardcoded as the bind address in both
  `main.go` files — never `0.0.0.0`. Only `-port` is a flag; the bind
  host is not configurable, by design.
- **Port conflict:** preflight checks 18080 and 18081 independently
  (`port_18080`, `port_18081`); an occupied port is a **startup failure**,
  not a fallback-to-another-port.
- **Health check:** `GET /health` on both, returns `200 {"status":"ok"}`
  immediately — well within Section 8.5's "must return 200 within 5s of
  startup before testing phase begins" (the handler does no I/O).

## Shutdown

- `process_supervisor.py` sends `SIGTERM` at "testing" phase exit.
- Both services catch `SIGTERM` (and `SIGINT`, for local/manual runs) and
  call `http.Server.Shutdown()` with a 10-second context timeout —
  in-flight requests are allowed to drain.
- If the process has not exited after 10 seconds, `process_supervisor.py`
  sends `SIGKILL`. Neither Go binary implements the `SIGKILL` side of
  this (a process cannot catch `SIGKILL` by definition) — that is
  strictly the supervisor's responsibility.

## Scope enforcement (`scope_guard.go`)

Both services load an identical `ScopeGuard` from `-scope-json` and check
`IsAllowed(target_url)` **before opening any outbound connection** —
`race_engine` before firing any of its N concurrent requests,
`smuggling_engine` before dialing either variant. An out-of-scope
`target_url` returns `403` and no outbound connection is ever attempted.

`*.example.com` matches both `example.com` (exact) and any subdomain
(`sub.example.com`, `deep.sub.example.com`) via a dot-bounded suffix
check — `evilexample.com` and `example.com.evil.com` do **not** match
(see `scope_guard_test.go` in both directories for the exact boundary
cases).

## CI: byte-identical scope guards

```
make -C services ci-scope-diff
```

Runs `diff race_engine/scope_guard.go smuggling_engine/scope_guard.go`.
Any drift is a CI failure (Section 8.5). This is the automated backing
for the `go_scope_diff` preflight check. Do not hand-edit one copy
without copying the change verbatim to the other — `ci-scope-diff` is
the safety net if that discipline slips, not a substitute for it.

## Other Makefile targets

```
make -C services build   # go build ./... in both services
make -C services vet     # go vet ./... in both services
make -C services test    # go test ./... in both services
```

## Dependencies

Both services are **stdlib-only** — no `go.sum`, no third-party
packages, no `GOPROXY` configuration needed. `go.mod` in each pins
`go 1.22` (the version actually installed on this project's dev/build
environment), not an aspirational later version — a `go` directive ahead
of the installed toolchain triggers Go's automatic toolchain download on
first build, which is not guaranteed to be reachable from every build
environment this project runs in.
