# Documented Additions & Scope Decisions

Implements: traceability requirement from the Engineering Constitution
("STOP CONDITIONS" / "Quote the conflicting sections, state the
ambiguity precisely"). Everything below is either a genuine blueprint
gap filled during implementation, or a deliberate scope boundary for
the week it was written in — flagged here so it survives independently
of chat history. One running numbered list across all weeks (not
restarted per week) so cross-references (e.g. item 2's header comment
pointer, item 9 below citing item 4's resolution pattern) stay
unambiguous as weeks accumulate.

---

## Week 0

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

**Correction (found during Week 1 review, re-running the actual
preflight CLI rather than trusting this list):** this item's list is
incomplete by one. `zstandard` ALSO fails in every sandbox that reaches
this repo state (`python3 -m cli.main` → `[FAIL] zstandard No module
named 'zstandard'`), but for a different reason in kind from the eleven
above: those eleven fail because a *service/binary this specific
sandbox doesn't provision* is missing (Redis, Postgres, Ollama, nuclei,
network egress, keyring backend). `zstandard` fails because it is
**not declared as a dependency anywhere yet** (absent from
`requirements.txt`, which is intentionally scoped to Week 0 imports
only) — it would fail identically in ANY environment, including the
real target one, until whichever week actually needs it
(`intercepting_client.py`'s 8 KB-body-cap/telemetry path, Week 5, is
the most likely first consumer per Section 3) adds it to
`requirements.txt`. `scipy` passes here only because it happens to be
preinstalled in this particular sandbox's base image, not because it's
a declared dependency either — same latent gap, currently masked. Net:
current real count is **11 passed / 13 failed / 2 not-yet-implemented
(of 26)** — verified by direct execution, not computed by hand from
this list (see Week 1 completion report). Both `zstandard` and `scipy`
should be added to `requirements.txt`, pinned, when their owning week's
code lands — flagged, not fixed here, since adding an unused pinned
dependency ahead of its consumer would itself be a small "build ahead."

## 8. Deferred preflight checks (pre-agreed, not new)

`webhook_binding` (needs `core/triggers/webhook_trigger.py`, Week 2) and
`scanner_ram_gate` (needs `SCANNER_REGISTRY`, Week 5, and the 29
scanners, Week 7) report `NOT_YET_IMPLEMENTED` rather than PASS or FAIL.
Both go live automatically once their target code exists — no registry
change needed then.

---

## Week 1

## 9. `core/governance/token_throttler.py` — no explicit week in Section 12 (same gap class as item 4's BudgetProfile)

Grep-verified: `token_throttler.py` has zero hits inside Section 12's
build-order table (lines 1915–1944). It IS named elsewhere —
Section 2.5 (ContextWindow budget display), Section 8.4 (budget
enforcement), Section 9.3 (LLM model roles), and R-M1's fix text
("`token_throttler.py` enforces a single STANDARD counter; there is no
separate BlueAgent budget") — but none of those sections assign it to a
week either. Same shape as item 4's `BudgetProfile` gap, resolved the
same way: identify the actual first consumer, land it there.

Its first real consumer is unambiguous: Week 1's own line item is
"budget dashboard (STANDARD/DEEP shared counter + BlueAgent subset
display)" (Section 12 Week 1 row) — a dashboard has nothing to display
without something tracking calls. **Resolution: `token_throttler.py`
lands in Week 1**, as the direct backing store for Week 1's own named
budget-dashboard deliverable. Built this week accordingly (STANDARD
counter shared by chain+BusinessLogicAgent+BlueAgent, cap 120; DEEP
counter, cap 10; R-M1's "no separate BlueAgent counter" rule enforced
by construction — there is exactly one `_standard_calls` field, and
BlueAgent's contribution is tracked in a second, display-only field
that is never compared against a cap of its own).

Scope boundary: this only builds the counters and the hard-cap
enforcement (raising once a cap is hit). It does NOT build the actual
Gemini/Groq/local-7B calling code that would invoke
`record_standard_call()` / `record_deep_call()` in the first place —
no week's Section 12 line item builds those call sites yet either
(StrategicPlanner, MentalModelBuilder, ChainAgent, BusinessLogicAgent,
BlueAgent, ExploitConfirmationAgent each arrive in their own later
weeks and will call into this throttler once they exist). Flag if
you'd rather this stayed an empty stub until one of those call sites
exists to justify it.

## 10. `core/ontology/findings.py` — `ExploitCandidate` and `PoC` deferred

**`ExploitCandidate` — dual-location ownership conflict, not just a missing spec.**
Verified directly against Section 3 (lines 253–268): `surface.py`'s tree
comment lists its own types ("EndpointSignals, SurfaceData, AttackEdge,
AttackGraph") and then, as a separate bare line, "`# ExploitCandidate
fields`" — a two-word pointer, not a definition. `findings.py`'s tree
comment independently lists "Finding, Evidence, `ExploitCandidate`, PoC,
EvidenceChain, TriageResult" as if `findings.py` owns it. Neither
location gives a single field name, and there is no arbitration between
the two claims. The contrast makes this conspicuous rather than a
plausible oversight: `JSAnalysisResult`, immediately below the
`ExploitCandidate` mention in `surface.py`'s own comment, gets a full
`@dataclass` breakdown (`source_url`, `endpoints_found`,
`secrets_found`, `dom_sinks`, `frameworks`, `source_map_url`,
`analysis_timestamp`); `Reviewability`, immediately below the
`ExploitCandidate` mention in `findings.py`'s own comment, gets the same
full treatment. The blueprint's own convention, applied twice in the
same two tree entries that mention `ExploitCandidate`, is to fully
enumerate a type when it means to specify one — `ExploitCandidate` gets
neither treatment, in either place. This is the same *shape* of bug as
`v6.4-003`'s `ToolSelector` double-booking (one component's real
workload claimed/implied by two different budget tables with no
tiebreaker until an explicit correction pass resolved it to one) —
a type claimed by two files, no tiebreaker, until someone decides.

**Status: flagged for explicit resolution before Week 5** (Section 8.1:
`FastLane → BeliefGraph: ExploitCandidate list` is the first real
consumer) — same tracking tier as other pre-week-arrival blockers in
this document (e.g. item 2's `/race` wire contract). Two open questions,
not one: (1) which file actually owns the `ExploitCandidate` dataclass —
`surface.py` (where the only field-adjacent pointer lives) or
`findings.py` (where it's listed alongside the other ontology types)?
(2) what are its fields — Section 8.1's flow description ("FastLane →
BeliefGraph: ExploitCandidate list") and Section 7.10's usage
("`lfi_scanner.py`... returns 0 ExploitCandidates") imply it originates
from Fast Lane's scanners and feeds `BeliefGraph` node creation, which
narrows the shape but doesn't specify it. Not resolved here — inventing
fields for either open question would be exactly the silent invention
the Engineering Constitution's STOP CONDITIONS forbid, and this doesn't
block Week 1 (nothing this week consumes `ExploitCandidate`). Deferred
the same way Week 0 deferred 10 of 11 enums (item 4): will be added to
whichever file is confirmed as owner, never a parallel one, once
resolved — most likely alongside BeliefGraph (Week 4) or tool-ifying the
scanners (Week 5), whichever actually needs to construct one first.

**`PoC` — simpler case, no ownership conflict, just no spec anywhere.**
Only ever named once, in `findings.py`'s type list; every other mention
is a flow/behavior reference, never a competing ownership claim
("LLM-generated Python PoC verification scripts only", Section 10.7).
Deferred the same way, most likely alongside `poc_generator.py`'s real
logic or the sandbox (Week 6) — no pre-week-arrival flag needed since
there's no conflicting claim to arbitrate, only an absence.

Neither is required by any of Week 1's named deliverables
(`Reviewability`, `evidence.verified`, harnesses, `EvidenceChain.strength`,
substitute counting, `[DECISION]` logs, budget dashboard).

## 11. `core/ontology/findings.py` — `EvidenceChain.vuln_type` typed as `str`, not a new enum

Section 3 lists eleven ontology enums; none of them is a "VulnType" or
similar covering the 29 scanner names. Typing `vuln_type` as a new enum
would add ontology surface area the blueprint never names. Kept as
plain `str` (the same lower_snake_case keys already used throughout
`vuln_thresholds.yaml` / `vuln_weights.yaml` / the scanner list, e.g.
`"xss"`, `"sqli"`). `SCANNER_REGISTRY` (Week 5) becomes this field's
real source of truth for valid values once it exists; no validation is
enforced on this field yet.

## 12. `core/ontology/findings.py` — `Reviewability.blue_agent_verdict` defaults to `"REJECTED"`

Section 3 types this field as `Literal["CONFIRMED","DISPUTED","REJECTED"]`
— three values, no fourth "not yet reviewed" option — but a `Finding`
must be constructible before `blue_agent.py` (Week 7) has ever run.
Defaulted to `"REJECTED"`: fail-closed, consistent with
`passes_signal_gate`/`passes_blue_agent_gate` both defaulting `False` in
the same dataclass, and with this project's scope_enforcer/safety_gate
"deny unless proven otherwise" posture everywhere else in Section 10.
Flag if a sentinel value (a fourth literal, e.g. `"PENDING"`) is
preferred instead — that would be a one-line change to the `Literal`
and this default.

## 13. `core/ontology/findings.py` — `compute_triage_score` lives in ontology, not `core/verifier/autonomous_triage.py`

Section 6.8's scoring function is pure arithmetic over `TriageResult` —
defined in this same ontology file — with no I/O and no scanner-specific
behavior. `EvidenceChain.strength` (a Week 1 deliverable) needs
`triage_score`, which needs this function; putting it in
`core/verifier/` would make an ontology dataclass import from the
verifier layer, inverting the intended dependency direction (verifier
depends on ontology, never the reverse — "Ontology-first types" in the
Engineering Constitution). `core/verifier/autonomous_triage.py` as a
FILE — the component that actually EXECUTES L1 replay / L2 variant / L3
cross-scanner checks against live scanner output to produce
`TriageResult` values in the first place — stays Week 7 scope exactly
as Section 12 names it ("AutonomousTriage 9-state matrix," Week 7 row);
it will import `compute_triage_score` from here once built, rather than
redefining it.

## 14. `core/ontology/findings.py` — `Finding.is_reportable` implemented as a property, no dedicated `poc_gate.py`

Section 3's file tree names no module for the PoC Gate itself (Layer 7
/ Section 6.9's logic is described only as pseudocode, not attributed to
a specific file the way `evidence_chain.py` and `deterministic_verifier.py`
are). `is_reportable` is a pure, side-effect-free boolean derived
entirely from a `Finding`'s own state (Section 6.9's expression,
translated verbatim), so it is implemented as a `@property` on `Finding`
rather than inventing an unnamed `core/verifier/poc_gate.py`. Placement
choice, not a blueprint citation — flag if a dedicated module is
preferred; moving the property's body into a free function elsewhere is
a mechanical, low-risk change if so.

## 15. "`[DECISION]` logs" (Section 12 Week 1 row) does not refer to a literal tag

Grepped the full blueprint for the literal string `[DECISION]`: it
appears exactly once, in Section 12's own Week 1 row text. Every other
bracketed tag in the document is a *specific* one — `[LOW_CONFIDENCE]`,
`[VDP_TIER_CAP]`, `[LFI_WINDOWS_DEFERRED]`, `[REPORT_DEGRADED]`,
`[DEGRADED_MODE]`, `[HARDENED_TARGET]`, `[INTERCEPT_OVERFLOW]`,
`[MENTAL_MODEL_PARTIAL]`, `[BELIEF_DESER]` — none literally named
`[DECISION]`. Read `[DECISION]` in Section 12 as build_prompt.md's own
generic shorthand ("Every [DECISION] log point named in the blueprint
is an actual structured log call, not a comment") for *whichever* of
those specific tags belongs to a given week's components, not as a tag
to implement itself. Cross-referenced each specific tag against its
owning component: of the nine, only `[LOW_CONFIDENCE]` (`EvidenceChain.strength_label`,
Section 6.8) belongs to a Week 1 component. The other eight belong to
components landing in later weeks (`[VDP_TIER_CAP]` → `safety_gate.py`,
Week 2; `[LFI_WINDOWS_DEFERRED]` → `lfi_scanner.py`, Week 7;
`[REPORT_DEGRADED]` → `autonomous_reporter.py`, Week 7;
`[DEGRADED_MODE]` → the Gemini/Groq/local fallback cascade, which has
no owning week yet since none of the components that would trigger it
exist; `[HARDENED_TARGET]` → `calibration_guardian.py`, Week 10;
`[INTERCEPT_OVERFLOW]` → `intercepting_client.py`, Week 5;
`[MENTAL_MODEL_PARTIAL]` → `mental_model/builder.py`, Week 3;
`[BELIEF_DESER]` → BeliefGraph deserialization, Week 4) and are
implemented as real structured log calls when those weeks build their
owning components, not now.

## 16. `core/verifier/deterministic_verifier.py` — only sets `evidence.verified`, not `reviewability`

Section 3's file-tree comment reads "Sets `Finding.reviewability` AND
`Finding.evidence.verified`," but `Reviewability`'s own two gates are
explicitly attributed to OTHER components in Section 3's ontology
comment: `passes_signal_gate` "set during Fast Lane" (Week 5),
`passes_blue_agent_gate` "set during Verification by `blue_agent.py`"
(Week 7). Neither exists yet. `verify_finding()` this week only sets
`evidence.verified` (Section 6.8's completeness flag) and logs
`[LOW_CONFIDENCE]` where applicable (item 15); it passes
`finding.reviewability` through untouched. Revisit this file once Fast
Lane and `blue_agent.py` exist — it may end up calling into both rather
than either setting these fields directly itself, but that's a Week 5/7
question, not a Week 1 one.

## 17. `core/verifier/safe_exploit_harness.py` — Week 1 builds the framework only, not the 29 concrete harnesses

Section 12's Week 1 row says "all harnesses"; its Week 7 row says "All
29 scanner harnesses" — deliberately different phrasing, read as
meaningfully different scopes rather than a restatement. Week 7's row
also separately calls out `ssrf_verifier.py`'s IMDSv2 matrix and
`sqli_verifier.py`'s `boolean_differential_confirmed` collection as ITS
OWN deliverables — vuln-specific logic that cannot be written before
its scanner exists (Weeks 2–7). Resolution: Week 1 builds
`SafeExploitHarness`, the shared abstract base every one of the 29
per-vuln harnesses (Section 7.1–7.29) will subclass, with an
`__init_subclass__` guard requiring each subclass to declare its
`vuln_type`. The 29 concrete subclasses — and `sqli_verifier.py`,
`xss_verifier.py`, `ssrf_verifier.py`, `idor_verifier.py` specifically,
which Section 3 lists under `core/verifier/` but whose CONTENT Section
7 and the Week 7 row both describe in scanner-specific terms — are Week
7 scope. `execute()`'s signature (a bare `endpoint: str`) is flagged in
the file itself as provisional: once `ExploitCandidate` (item 10,
deferred) exists, Week 7 will likely need to pass richer per-candidate
context, changing this signature. Flag if you'd rather Week 1 not
build even the abstract base until Week 7, when its exact shape would
be informed by having a real subclass to write.

## 18. Forward-reference breadcrumb: `[REPORT_DEGRADED]` at Week 7's `autonomous_reporter.py`

Section 6.10, verbatim: "If Groq limit hit during reporting: fallback
to local 7B; log `[REPORT_DEGRADED]`." Section 8.3's cascade table has
the same entry (`GROQ_LIMIT_REPORTING`). The trigger condition
(`TokenThrottler.groq_report_reserve_exceeded`, item 9) exists as of
this week, but the component that would actually check it and log the
tag — `autonomous_reporter.py` — is Week 7 scope and does not exist
yet, so there is nothing to test now. Bread-crumbed directly at the
call site so it isn't dropped by the time Week 7 arrives: see the
`# TODO(Week7)` comment on `TokenThrottler.groq_report_reserve_exceeded`
in `core/governance/token_throttler.py`.

**Naming note:** an earlier instruction referred to this property as
`would_exceed_report_reserve()`. The actual property built this week is
`groq_report_reserve_exceeded` (a `@property`, not a callable method) —
flagging the mismatch explicitly rather than silently treating the two
names as interchangeable. The TODO comment and this entry both cite the
real name.

## 19. `core/control/llm_budget_dashboard.py` — renders only the "BUDGET" block, and `groq_strategy_max` has no blueprint-given number

Section 3 attributes the FULL ContextWindow template (Phase/Strategy/
MentalModel/BeliefGraph/ActiveChain/PendingActions/RecentOutcomes/Safety,
not just Budget — Section 2.5) to `core/cognitive/context_window.py`.
That file is not named in any Section 12 week either — same gap class
as `BudgetProfile` (item 4) and `token_throttler.py` (item 9), found
while building this week's dashboard. Not resolved now: nothing in Week
1 needs the rest of ContextWindow, only the Budget block, and the other
blocks depend on components (MentalModelBuilder, BeliefGraph,
ChainEngine) that don't exist until Weeks 3–7. Flagged so
`context_window.py`'s eventual owning week actually builds the other
seven blocks against this same file rather than a parallel one.

Separately: Section 2.5's own template shows `Groq strategy:
{groq_strat_used}/{groq_strat_max}` — `{groq_strat_max}` is a literal
unfilled placeholder, not a number, anywhere in the blueprint (unlike
`groq_report`'s hardcoded `/40` two words later in the same template
line). No section gives `groq_strategy_model` an explicit per-session
call ceiling. `render_budget_section()` takes `groq_strategy_max: int |
None` and renders the honest string `"no fixed cap"` when `None` rather
than fabricating a number (e.g. copying the ~5-calls-typical figure from
Section 9.3, which is a typical-case observation, not a stated cap).
Flag if you'd rather a specific number be adopted as the de facto cap —
Section 9.3's breakdown (StrategicPlanner 1 + MentalModelBuilder 4 = 5
typical, "~5–15" misc/retry unattributed to a specific tier) is the only
candidate basis for one.
