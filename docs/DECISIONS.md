# Week 0 — Documented Additions & Scope Decisions

Implements: traceability requirement from the Engineering Constitution
("STOP CONDITIONS" / "Quote the conflicting sections, state the
ambiguity precisely"). Everything below is either a genuine blueprint
gap filled during implementation, or a deliberate Week 0 scope
boundary — flagged here so it survives independently of chat history.

---

## 1. GitHub authentication for this project

GitHub auth for this project uses `gh` CLI device-flow login
(`gh auth login --hostname github.com --git-protocol https --web`), not
a stored token — this must be re-run once per fresh sandbox session
since nothing persists across sessions. Steps: `apt-get install -y gh`,
run the login command in the background, retrieve the one-time code +
URL from the log, report both to the human, wait for confirmation, then
`gh auth setup-git` before any push.

**Update (this session):** in practice, backgrounded processes did not
survive between separate tool calls in this build sandbox — two device
codes expired unused because the polling process was gone by the next
check, and a bounded in-call poll loop was killed outright by the
sandbox's own execution-time enforcement before it could complete. The
device-flow approach itself is sound and remains the right one for a
persistent environment; it just could not be driven to completion
end-to-end inside this particular ephemeral sandbox. **Fallback under
discussion, not yet settled:** exporting the committed repo (as a `git
archive` zip or a `git bundle` — TBD) for push from an environment with
durable git credentials instead. If a future session's sandbox behaves
differently (long-lived background processes survive), the original
protocol above should work as designed.

## 2. `/race` wire contract (services/race_engine/race.go)

**Status:** documented addition, not a blueprint citation. Flagged for
your review before Week 7 (`race_scanner.py`) becomes the actual caller.

The blueprint specifies the `parallel` field and its default (30,
Section 3) and the required evidence outputs (nanosecond timestamps,
`success_count`, Section 7.5), but no full request/response JSON schema
anywhere (confirmed by grep against the source blueprint: only
`POST /race {"parallel": race_parallel, ...}` appears — the same gap
class as `/smuggle`, resolved the same way, mirrored from your resolution
for item 1: Go performs transport-only work (fire N concurrent requests,
capture nanosecond timestamps, classify success via a caller-supplied
`success_status_codes` allowlist); the actual expected-vs-vulnerable
success-count verdict is left to `race_scanner.py`, since only it knows
what a given target's "successful redemption" response looks like. Full
schema and rationale: header comment in `services/race_engine/race.go`.

## 3. `/smuggle` wire contract (services/smuggling_engine/smuggling.go)

**Status:** documented addition, user-resolved and approved — recorded
here for the same traceability reason, not because it's in question.
Full schema: header comment in `services/smuggling_engine/smuggling.go`
and `services/README.md`.

## 4. `core/ontology/enums.py` — partial implementation

Section 3 names eleven enums for this module. Only `PayloadFileType` is
implemented, because `payload_inventory.py` (Week 0) is the only Week 0
consumer. The other ten (`FailureCause`, `RiskLevel`,
`WorkflowConfidence`, `HumanFeedback`, `MemoryEntryStatus`,
`CalibrationBand`, `EvidenceType`, `TargetType`, `BusinessValue`,
`BudgetProfile`) are intentionally deferred to the weeks that actually
consume them, per the Engineering Constitution's strict week-ordering
rule. They will be added to this same file, never a parallel one, as
their owning weeks arrive. Per-enum landing point, checked directly
against Section 12's build order table rather than assumed:
- `EvidenceType` → Week 1 (evidence_chain.py, `EvidenceChain.strength`)
- `BusinessValue`, `TargetType` → Week 3 (explicitly named in the Week 3 row:
  "`BusinessValue` in `info_gain_scorer.py`"; `TargetType` backs
  `TargetAdapter`, also Week 3)
- `BudgetProfile` → **no week explicitly names this in Section 12.** Its
  two actual consumers are `config.py` (broad/early — "AgentConfig + all
  Enums + BudgetProfile", Section 3) and `nuclei_runner.py` ("rate from
  BudgetProfile", Week 7). It will land whenever `config.py`/`AgentConfig`
  is first substantively built, or no later than Week 7 if that comes
  first.
- `FailureCause`, `RiskLevel`, `WorkflowConfidence`, `HumanFeedback`,
  `MemoryEntryStatus`, `CalibrationBand` → not yet mapped to a specific
  week; will be added when their first real consumer is implemented.

**Flag for whenever `BusinessValue` is actually implemented (Week 3/4):**
Section 3's own listing shows only 3 members (LOW/MEDIUM/HIGH), but
Section 11.3's BeliefGraph deserialization logic requires a 4th member —
`BusinessValue.UNKNOWN` — as the fallback when a corrupted checkpoint's
stored value fails to parse (R-L4 fix: `except ValueError: node["business_value"]
= BusinessValue.UNKNOWN`). Section 3 and Section 11.3 disagree on the
member count; `UNKNOWN` must be added as a 4th member or R-L4's own fix
doesn't type-check.

## 5. `configs/scope.yaml` — built with its full documented key set now

Week 0 code only reads `scope_domains` (the `scope_config_generator.py`
generator's only input). `program_type`, `race_parallel`,
`credential_validation_allowlist`, and `rate_limit_per_host` are
included now as one coherent file — matching how Section 3 documents
this file as a single block — rather than added key-by-key as their
owning weeks (2, 7, 6) land. Judgment call: this is config *data*, not
executable behavior, so it was treated as exempt from the
don't-build-ahead rule that applies to code components. Flag if you'd
rather it were trimmed to Week-0-only keys.

## 6. `core/governance/scope_config_generator.py` — file placement

The blueprint names the *behavior* ("scope_json_gen: generates
services/scope_allowed.json from scope.yaml", Section 3) but never names
the specific module that implements it. Placed under
`core/governance/` since that's where the rest of scope enforcement
already lives (`scope_enforcer.py`, `safety_gate.py`) in the blueprint's
own tree — a placement choice, not a blueprint citation.

## 7. Preflight checks not live-verifiable in this build sandbox

`redis_connection`, `postgres_connection`, `asyncio_redis`,
`chromadb_conn`, `nuclei_binary`, `nuclei_templates`, `ollama_model`,
`interactsh_conn`, and the OS-keyring-backed checks
(`keyring_secrets`, `groq_strategy_model`, `groq_report_model`,
`gemini_model`) all correctly FAIL CLOSED in this sandbox — the
underlying services/binaries/keyring backend/network egress simply
aren't provisioned here (see `services/README.md` and the Week 0
completion report for the exact list). This is expected sandbox
behavior, not a code defect; each check's *logic* is covered by mocked
unit tests. Re-run `python3 -m cli.main` in the real target environment
once Redis/Postgres/Ollama/nuclei are installed and real API keys are in
the OS keyring.

## 8. Deferred preflight checks (pre-agreed, not new)

`webhook_binding` (needs `core/triggers/webhook_trigger.py`, Week 2) and
`scanner_ram_gate` (needs `SCANNER_REGISTRY`, Week 5, and the 29
scanners, Week 7) report `NOT_YET_IMPLEMENTED` rather than PASS or FAIL.
Both go live automatically once their target code exists — no registry
change needed then.
