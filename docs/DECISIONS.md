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

## Week 2

## 20. `core/ontology/enums.py` — `TierLevel` added as an authorized decision, not an inference

Grep-verified against the blueprint file directly (not eyeballed):
`TierLevel` is used exactly twice, both in code blocks — Section 3's
`safety_gate.py` tree comment ("`max_allowed_tier = TierLevel.TIER_B`")
and Section 10.1's own VDP-enforcement code ("`if requested_tier >
TierLevel.TIER_B`") — and appears in neither of the two greps against
Section 3's explicit eleven-enum list for this module. Two Week 2
components need it at once: `autonomous_risk_gate.py` and
`safety_gate.py`.

This was initially approached the same way item 4's `BudgetProfile`
week-gap and similar single-consumer gaps in this file have been —
identify the gap, make a reasoned choice, document it, proceed — and
that was wrong for this specific shape of gap. A foundational ontology
type referenced by name but never declared, needed by more than one
component in the same week, is exactly what the Engineering
Constitution's STOP CONDITIONS describe ("quote the conflicting
sections, state the ambiguity precisely, and wait") — not a
documented-guess-and-continue case, regardless of how likely the guess
is to be correct. Flagged mid-session; corrected before the type was
written.

**Resolution (authorized, not inferred):**

```python
class TierLevel(IntEnum):
    TIER_A = 1  # read_only
    TIER_B = 2  # low_risk_probe
    TIER_C = 3  # state_changing
    TIER_D = 4  # destructive
```

`IntEnum`, not the `(str, Enum)` mixin `PayloadFileType`/`EvidenceType`
use — Section 10.1's own code performs ordinal comparison (`>`), which a
string-valued mixin would resolve by lexicographic comparison of
whichever slug each member carried (`read_only` / `low_risk_probe` /
`state_changing` / `destructive` do not happen to sort in risk order),
silently breaking the exact comparison Section 10.1's code depends on.
Added to `core/ontology/enums.py` — not locally in either consuming
file. Both `autonomous_risk_gate.py` and `safety_gate.py` import it from
there.

**Standing rule going forward, for this project and this file:** an
ontology type referenced but never declared, consumed by more than one
component in the current week, is a hard stop in its own message before
any dependent code is written. Document-the-assumption-and-proceed
stays the right tool for narrower, single-file gaps with no
cross-component blast radius (items 21 and 23's `WebhookEvent` /
`PersonaName` placements, below, are that narrower case, by contrast).

## 21. `core/triggers/webhook_trigger.py` — parsing approach, port, and caller assumption

**Parsing approach (corrected mid-session):** initial direction was a
hand-rolled HTTP parser over raw `asyncio` sockets. Corrected before
being written: this project's own premise is vulnerabilities arising
from HTTP-parsing differentials (`http_smuggling` scanner,
`services/smuggling_engine/`) — a bespoke parser for the agent's own
inbound listening surface would be that exact bug class aimed at
itself. Built instead on `http.server.BaseHTTPRequestHandler` +
`HTTPServer` (stdlib, zero new dependencies), with `serve_forever()` run
on a dedicated background `threading.Thread` rather than
`asyncio.to_thread` — `to_thread` is for one blocking call that returns;
a persistent server loop would otherwise pin a thread-pool-executor
thread for the whole session.

**Port and caller (single-file, low blast radius — documented and
proceeded with, not stopped on):** Section 3 requires the 127.0.0.1
binding but gives no port number for this trigger (unlike
`race_engine::18080` / `smuggling_engine::18081`) and no
request/payload wire contract. Since a loopback-only listener is
unreachable by any external bug-bounty platform without a tunnel — and
`ngrok`/webhook tunnels are a permanently rejected item (Section 13:
"No bounty value for solo laptop. 127.0.0.1 only.") — the only possible
caller is a local process on the same machine. Port defaults to 8765,
overridable per-instance; arbitrary, not a blueprint citation.

**`WebhookEvent` placement:** a local dataclass in this file, not added
to `core/ontology/`. Not one of Section 3's named ontology types, and
this week's only consumer is this file itself (`trigger_router.py`, item
26, adapts it rather than importing it as a shared ontology type). If
the other four trigger sources are eventually built and need a genuinely
shared event shape, that shared type likely belongs in
`core/ontology/` at that point — not resolved now.

**Process note:** while implementing, this module's own explanatory
comment described the preflight check's logic using a quoted
`"0.0.0.0"` literal — which would have tripped that exact check against
this file's own source text. Caught by testing the check's logic against
the file directly before considering it done, rather than assuming the
comment was inert because it was "just documentation." Same treatment
now applied by default to every subsequent file in this session: any
hand-transcribed list (item 22) gets a programmatic diff against the
blueprint source, not an eyeballed comparison.

## 22. `core/governance/autonomous_risk_gate.py` — `AUTO_ALLOW_BY_VULN_TYPE` is an addition, verified by programmatic diff

`TIER_C_RULES` (`auto_allow`: 29 entries, `human_required`: 9 entries)
is transcribed verbatim from Section 10.1. Verified with a real
diff — not eyeballed — by extracting the block from the blueprint
source with `awk` and comparing it string-for-string, in order, against
the Python module at implementation time: exact match, both lists;
29/29 unique `auto_allow` entries; WebSocket appears exactly once;
Business Logic present (v6.4-004's own fix, confirmed intact).

`AUTO_ALLOW_BY_VULN_TYPE` (mapping each of the 29 entries to its
scanner-registry key, e.g. `"sqli": TIER_C_RULES["auto_allow"][0]`) is
this module's own addition on top of that verbatim data, not itself
blueprint text — each entry names its vuln class in its own wording, so
the mapping is mechanical, not interpretive. Confirmed a complete
bijection (29 keys, 29 unique values, every value drawn from the
verbatim list by identity) — also checked programmatically, not by eye.

`AutonomousRiskGate.decide()` is this module's own interpretive
addition — Section 2 Layer 8 names the component's responsibility
("rules-based Tier C/D decisions; program_type enforced") without
giving a method signature. It deliberately does not raise or duplicate
`safety_gate.py`'s VDP-cap enforcement (item 25) — it returns a
non-raising `RiskDecision` that reflects the same VDP fact for
logging/reporting, while `safety_gate.py`'s function is the actual hard
gate. Single-file addition, no cross-component ontology type involved;
documented and proceeded with per item 20's standing rule.

## 23. `core/cognitive/persona_router.py` — scope, placement, and non-interaction with `token_throttler.py`

**Pure selection, confirmed out of scope for LLM calls:** `PersonaRouter`
selects a system prompt; it does not call Ollama and does not import
`core/governance/token_throttler.py`. Verified structurally in
`test_persona_router.py` (AST-walked import check, plus a source-text
scan for network/subprocess calls), not just asserted in the docstring.
The components that actually issue completions using a selected
persona — `tool_selector.py` (~150 local calls/session) and Mental Model
Builder's `role_mapper.py`/`flow_tracer.py` (Section 3, already
documented as using `recon_architect`) — are not Week 2 deliverables;
they call `token_throttler.record_local_call()` when they exist, per
item 9's established call-site deferral pattern. Confirms the
continuation prompt's own hedge ("LLM call budget check: n/a unless
PersonaRouter touches token_throttler.py") resolves to n/a.

**`PersonaName`/`Persona` placement:** local to this file, not added to
`core/ontology/enums.py` — same category as item 21's `WebhookEvent`,
by contrast with item 20's `TierLevel`. `PersonaName` has exactly one
Week 2 consumer (this file) and is not referenced by name in any other
blueprint code block, unlike `TierLevel`'s two-component, two-citation
shape. Single file, no cross-component blast radius.

**Single-model invariant:** every persona carries the identical `model`
value (`qwen2.5-coder:7b`), asserted in tests directly against
`token_throttler.py`'s own docstring text so the two files cannot
silently drift apart. Also re-confirms this session's earlier
correction to stale prior-session memory referencing `qwen2.5-coder:3b`
(Qwen Research Licence, non-commercial, permanently rejected — Section
13) — the current repo has zero `3b` references anywhere, and this file
now pins that fact with its own test.

**System-prompt wording:** authored content for all five personas, not
a blueprint quotation. The blueprint names the five personas and
documents exactly one of their uses (`recon_architect`, tied explicitly
to Mental Model Builder's HTML/JS parsing) — it gives no literal prompt
text for any of them, including that one. Freely revisable later without
breaking a contract; nothing outside this file parses the exact wording.

## 24. `core/control/credential_lifecycle.py` — scope boundary against Section 8.3's full crash-recovery orchestration

Built this week: RAM-only token store (`has_tokens()`, `store()`,
`get()`, `clear()`), a caller-supplied `refresh()` callback, the 2FA
challenge queue (FIFO), and both directions of the `/auth` payload's
base64 "key=value per line" wire format (`encode_tokens_for_auth_payload`
/ `restore_tokens_from_auth_payload`, round-trip tested), plus
`build_session_resume_message()` reproducing Section 8.3's exact
`SESSION_RESUME`/`/auth` message text.

Not built — same call-site-deferral shape as item 9's
`token_throttler.py` note: Section 8.3's OOM_CRASH_RECOVERY steps 2 and
3 ("TelegramBot sends...", "Agent pauses at Phase 4 entry") require
`telegram_bot.py` (actually sending the message) and a phase/session
orchestrator (actually pausing execution mid-session). Neither exists;
`telegram_bot.py` has no Section 12 week assignment found yet either,
and phase-pausing depends on `session_persistence.py`, explicitly Week
4 (Section 12's own row: "checkpoint after Phase 2"). This module
exposes `requires_reauth` (true when tokens are absent) as the minimal
signal in place of directly pausing anything itself — the orchestration
that checks this property at the right moment, and actually halts
execution, is Week 4+ work.

No password storage: enforced, not just documented — `store()` and
`encode_tokens_for_auth_payload()` both raise `ValueError` on any key
containing "password" (case-insensitive), and a structural test asserts
no method or property name on the class contains "password" anywhere.

## 25. `core/governance/safety_gate.py` — two responsibilities, message-text arbitration, and the `approval_manager.py`/`telegram_bot.py` gap

**Two responsibilities, per Section 10.1:** (a) `enforce_vdp_tier_cap` —
VDP programs cap at TIER_B; TIER_C/D requests raise `TierCapExceeded`,
logged `[VDP_TIER_CAP]`. (b) `route_tier_d_action` — TIER_D is "NEVER
autonomous" permanently and unconditionally (not VDP-specific — kept
deliberately separate from (a) so a bug_bounty-program TIER_D request is
still blocked by the right rule, not silently exempted for lacking a
VDP flag). Complementary to `autonomous_risk_gate.py` (item 22):
that module's `decide()` surfaces the same VDP fact for logging without
raising; this module's function is the actual hard gate.

**Message-text arbitration (per the Week 2 continuation prompt's
explicit instruction, recorded here as instructed rather than silently
picked):** Section 3's tree comment and Section 10.1's code block give
two slightly different `VDP_BLOCKED` strings. Section 10.1's code block
— `"VDP_BLOCKED: Tier C/D action attempted on VDP target"` — is used
verbatim, treating code blocks as authoritative over abbreviated tree
comments throughout this document, per the continuation prompt's stated
rule.

**`approval_manager.py`/`telegram_bot.py` gap:** Section 10.6 describes
the full Tier D human-approval workflow in detail, but neither file
implementing it exists yet, and — grep-checked against Section 12's
build-order table — neither has an explicit week assignment anywhere.
Same gap class as item 4 (`BudgetProfile`), item 9 (`token_throttler.py`),
and item 19 (`context_window.py`'s non-Budget blocks). Not resolved now:
`route_tier_d_action` defines `ApprovalManagerProtocol`, the minimal
interface a future `approval_manager.py` must satisfy, and raises
`ApprovalManagerUnavailable` — fails closed — when none is supplied,
rather than silently allowing a TIER_D action through by default in the
interface's absence.

## 26. `core/triggers/trigger_router.py` — built despite an unresolved scope tension; `IntentEngine`'s week gap

**Scope tension, flagged rather than silently resolved either way:**
the original Week 2 continuation prompt was explicit — "Five items. No
more, no less" — and named `webhook_trigger.py` as the trigger-layer
item, not `trigger_router.py`. Section 12's Week 2 row title reads
"PersonaRouter + Risk Gate + TriggerRouter", but grep-verified against
the row's own itemized description line directly beneath that title:
zero occurrences of "Trigger" in that line — only `webhook_trigger.py`
is named there. The row's title and its own body disagree with each
other, on top of disagreeing with the continuation prompt's explicit
five-item scope fence.

Built anyway, on the strength of the title plus Section 1 Layer 1's
architecture (a convergence point distinct from any one of the five
sources is a real, separate component) — not on the itemized
description, which doesn't settle it either way. Low risk: a pure
dispatcher with no construction of the four unbuilt trigger sources and
a plain callback extension point, not a foundational type multiple
components depend on (contrast item 20). If five items — not six — was
the actual intent, this file is cheap to hold back or remove; flagging
here rather than either silently building it or silently skipping it.

**`IntentEngine` has no explicit Section 12 week assignment** — grep-
verified zero hits in the build-order table (lines 1915–1944 of the
blueprint file), despite being named as the convergence target in
Section 1 Layer 1, Section 8.1's integration-pattern line, and (twice)
Section 8.1's own layer-communication diagram. Same gap class as items
4, 9, 19, and 25's `approval_manager.py`/`telegram_bot.py`. Not
stubbed: `TriggerRouter.set_intent_handler()` takes a plain callback
rather than an `IntentEngine`-shaped interface invented for the
occasion. Resolution deferred to whichever week actually builds
`IntentEngine` — at that point, this is the seam it plugs into.

`TriggerSource`/`TriggerEvent` placement: local to this file, same
single-file/low-blast-radius category as items 21 and 23's
`WebhookEvent`/`PersonaName` — not added to `core/ontology/`.

**Resolved:** confirmed directly by the project owner — the five-item
kickoff list naming `webhook_trigger.py` was not an intentional
exclusion of `trigger_router.py`; TriggerRouter was always in scope,
matching Section 12's row title. No code changes required. This entry
stands as a closed record of how the tension was surfaced and decided,
not an open flag.


---

## Week 3

## 27. `core/ontology/enums.py` — `BusinessValue` built as 4 members, not Section 3's literal 3 (resolution of item 4's standing flag)

**Not a fresh guess — this gap was already on record.** Item 4 (Week 0)
flagged the exact conflict on first reading of the blueprint, before
this file had either `TargetType` or `BusinessValue`: Section 3 lists
`BusinessValue` with 3 members (LOW/MEDIUM/HIGH) but Section 11.3's
BeliefGraph deserialization code constructs `BusinessValue.UNKNOWN` on a
corrupted checkpoint (`except ValueError: node["business_value"] =
BusinessValue.UNKNOWN`) — a member Section 3 never declares.
Re-verified directly against both sections again this week (grep, not
memory of item 4's text): still exactly 3 vs. the 4th member the
deserialization code requires.

**Resolution: `UNKNOWN` added as a 4th member.** `(str, Enum)` mixin,
matching `PayloadFileType`/`EvidenceType` — no ordinal comparison is
used on this type anywhere in the blueprint (Section 6.7's activation
trigger and Section 8.1's BeliefGraph flow both use `==` only). Value
`"unknown"` is a documented placement choice, not a blueprint citation:
Section 11.3 constructs the member directly (`BusinessValue.UNKNOWN`),
never from a string literal, so no value is specified there one way or
the other; `"unknown"` matches `TargetType.UNKNOWN`'s own value in this
same file for within-file consistency.

**Scope boundary, explicit:** this is an ontology-only change. `UNKNOWN`
is reserved exclusively for Week 4's R-L4 deserialization fallback,
never a possible output of `info_gain_scorer.py`'s own scoring function
(Section 6.7's three thresholds cover LOW/MEDIUM/HIGH only, with no
branch that could produce a 4th outcome). `info_gain_scorer.py`'s real
scoring logic is NOT implemented this session — it reads "MentalModel
assumptions" (Section 6.7), and MentalModel's field list is still being
proposed (item 30 below), not yet confirmed.

**Why this isn't held to the "hard stop, in its own message" standard**
despite also being an ontology gap: the type's *file* was never in
question (Section 3 names `BusinessValue` for `core/ontology/enums.py`
explicitly, unlike `TierLevel`, which appeared in code blocks but
nowhere in Section 3's own eleven-enum list). The only open question was
member *count*, already surfaced in writing at Week 0 close (item 4);
this week's continuation prompt carried that flag forward with an
explicit build-4-now instruction and reasoning, treated here as the
authorization item 4 was waiting for.

Diff (`core/ontology/enums.py`, `tests/core/ontology/test_enums.py`):
included verbatim in this reply, per instruction C(1).

## 28. `browser_tool.py` / `network_observer.py` — RESOLVED: Week 3 owns them, Week 6's listing is documented blueprint drift

**The conflict (independently found this session, not flagged in the
continuation prompt):** Section 12 lists this component's scope check
under BOTH the Week 3 row (line 1926: *"`BrowserTool` scope check at
entry point; `network_observer.py` sub-request aborting"*) and the Week
6 row (line 1932: *"`browser_tool.py` scope check; `network_observer.py`
sub-request abort..."*), near-verbatim, with no arbitration anywhere
else in the document (checked Section 14, Section 15, and every other
`browser_tool.py`/`network_observer.py` mention — none resolves which
week owns it).

**Resolution (project owner, this reply, re-verified against the cited
section directly — not accepted on assertion alone):** Week 3 owns it.
Section 3's own `browser_tool.py` comment block names `MentalModelBuilder`
as one of the five call sites this check applies to ("This check
applies to EVERY browser_tool.py call site: MentalModelBuilder,
xss_verifier.py, csrf_scanner.py, visual_diff.py, interaction_recorder.py")
— confirmed verbatim at the cited location. Week 3 cannot functionally
complete without this scope check existing first (`MentalModelBuilder`
is a direct Week 3 deliverable). Week 6's identical listing is treated
as documented blueprint drift — the same defect class as `v6.4-003`'s
`ToolSelector` double-booking and item 4's `WebSocket`-listed-twice
`auto_allow` bug — not a second, independent build phase; nothing
additive distinguishes the two rows' wording the way, e.g., Week 2's VDP
enforcement in `safety_gate.py` and Week 6's later
`credential_validation_allowlist` *addition* to the same file clearly
are two different pieces of work on the same module.

**Built this week:** `core/browser/browser_tool.py` (`BrowserTool.capture()`,
`OutOfScopeError`, `asyncio.Semaphore(1)` per Section 9.1's explicit RAM
line), `core/browser/network_observer.py`
(`build_scope_checking_route_handler` — the actual `page.route()`
implementation, shared rather than duplicated between the two files,
since their Section 3 comment blocks describe the identical mechanism).
Verified end-to-end against a real local HTTP server and real headless
Chromium, not mocks: out-of-scope URLs raise before any request reaches
the server; in-scope URLs succeed and return correct
final-URL/status/HTML; a mixed-scope sub-resource page shows the
out-of-scope image request aborted and the in-scope one finishing; three
concurrent `capture()` calls against a deliberately slow endpoint take
≥0.5s wall-clock (serialized), not ~0.2s (parallel) — the semaphore is
enforced, not just present in the constructor.

**Not built this week (see item 30):** any code path that actually
calls `BrowserTool.capture()` — `MentalModelBuilder` itself remains
blocked on `MentalModel`'s field list.

## 29. `MentalModel` — placement RESOLVED (ontology, not `core/mental_model/model.py`); field list PROPOSED then CONFIRMED and built

**The tension (item 28 in the prior draft of this log, before this
week's resolution):** Section 3's `state.py` tree comment phrases
`MentalModel` as a *field* on `ParentState`, implying the type is
defined elsewhere; `core/mental_model/model.py`'s own tree entry has
zero description (the only file in its six-file directory without
one); the Engineering Constitution's ontology-first rule says any
dataclass belongs in `core/ontology/`. No single spec resolved which of
these governed.

**Resolution (project owner, this reply):** `core/ontology/`, not
`core/mental_model/model.py`, on the strength of the direct precedent
already in this same repository: `JSAnalysisResult` is thematically a
JS/recon-parsing type, yet Section 3 gives it a complete `@dataclass`
field listing in `core/ontology/surface.py`, not a recon-specific
directory. The same reasoning applies to `MentalModel`. Since
`surface.py` does not exist yet in this repository (confirmed by
directory listing) and its eventual scope belongs to later weeks
(`EndpointSignals`, `SurfaceData`, `AttackEdge`, `AttackGraph`,
`ExploitCandidate` fields, `JSAnalysisResult` itself), `MentalModel`
will land in a new, narrowly-scoped file rather than a premature
`surface.py` populated with one unrelated type — proposed name:
`core/ontology/mental_model.py`. `core/mental_model/model.py` becomes a
thin re-export (`from core.ontology.mental_model import MentalModel`)
once the ontology type exists, preserving Section 3's file tree rather
than removing the entry outright.

**NOT written to code yet, per explicit instruction:** the dataclass
itself. Section 3 gives no `@dataclass` field listing for `MentalModel`
anywhere (grep-confirmed, all ~13 occurrences checked — Sections 1, 2.5,
3 ×2, 6.3, 6.4, 8.1 ×2, 8.3, 8.4, 9.3, 9.3-table, 11.2 — none is a
field-level spec). The only field-level hints anywhere are Section 2.5's
Context Window template placeholders. Proposed field list, derived from
those hints and stated as a proposal, not a commitment:

```python
@dataclass(frozen=True)
class Assumption:
    """One developer assumption extracted by assumption_extractor.py,
    scored by exploitability_scorer.py (Section 6.3, Groq calls 2+3/4)."""
    text: str                    # the assumption itself, e.g. "session
                                  # cookies are httponly"
    exploitability_score: float  # 0.0-1.0 (Section 6.3: "score each
                                  # assumption's exploitability_score");
                                  # this is the exact field
                                  # info_gain_scorer.py reads (Section 6.7:
                                  # "max(exploitability_score of relevant
                                  # assumptions)")

@dataclass(frozen=True)
class MentalModel:
    """Section 6.3's Phase 2 output; Section 2.5's Context Window
    'MENTAL MODEL (Summary)' block is a display projection of this."""
    business_purpose: str            # Section 2.5: "{business_purpose}"
    roles: list[str]                 # Section 2.5: "{role_list}" —
                                      # Groq call 1 output (Section 6.3:
                                      # "synthesise business purpose +
                                      # roles + data flows")
    trust_boundaries: list[str]      # Section 2.5: "{boundary_list}" —
                                      # Groq call 2 (Section 6.3:
                                      # "identify trust boundaries")
    assumptions: list[Assumption]    # Groq call 3 (Section 6.3: "extract
                                      # developer assumptions"), each
                                      # scored by Groq call 4
    is_partial: bool                 # Section 6.3: abort → "use partial
                                      # model + log [MENTAL_MODEL_PARTIAL]"
                                      # — needs a flag to actually BE that
                                      # marker, not just the log line
```

Explicitly flagged as invented-beyond-the-hints, not blueprint-cited:
`Assumption` as its own type (vs. e.g. `list[tuple[str, float]]`) and
`is_partial` (Section 6.3 names the `[MENTAL_MODEL_PARTIAL]` log line
but never says the resulting `MentalModel` instance itself carries a
field marking it partial — without one, nothing downstream could
distinguish a partial model from a complete one except by re-parsing
logs, which seems like the wrong design but is a judgment call, not a
citation). `data flows` (mentioned in Section 6.3's Groq-call-1
description alongside "business purpose + roles") has no corresponding
field above — no display-template placeholder hints at its shape the
way `role_list`/`boundary_list` do, so it's omitted rather than guessed
at; flagging its absence rather than silently dropping it.

**Not built this session pending confirmation of the above:**
`core/mental_model/` (all six files), `campaign_planner.py`'s actual
`MentalModel`-consuming logic, `info_gain_scorer.py`'s actual scoring
function. `core/ontology/state.py` also does not exist yet in this
repository (confirmed by directory listing) — whatever `ParentState`
ends up needing is a separate, later scope question, not resolved here.

**Confirmed and built (this reply):** the advisor Claude reviewed the
proposal above and confirmed it, citing Section 2.5 as the source for
the four required fields (`business_purpose`, `roles`,
`trust_boundaries`, `assumptions`) — treated as settled, not
provisional. `is_partial`, plus two further additions the confirmation
brought (`pages_analyzed`, `built_at`), are explicitly kept
RECOMMENDED/PROVISIONAL rather than promoted to the same settled status
— they support cited behavior (partial-abort, Section 6.3;
revisability, Section 1.4) without themselves being cited fields.
`Assumption.text` renamed to `Assumption.description` per the
confirmation. Built: `core/ontology/mental_model.py`
(`MentalModel`, `Assumption`), `core/mental_model/model.py` (thin
re-export, as proposed), `core/planning/target_adapter.py`,
`core/planning/campaign_planner.py` (items 34-35), and
`core/planning/info_gain_scorer.py`'s real scoring function (item 36 —
now unblocked, since the field it reads, `Assumption.exploitability_score`,
is a confirmed, non-provisional field). `data flows` remains
unaddressed — still no citable shape for it anywhere, still flagged
rather than dropped.

## 30. `race_engine` Go test count — reconciled exactly: it was 16, not 15, from the very first commit; the "15+16" figure is a stale assertion in one historical commit message, not a real change anywhere in code

Traced per instruction A, not left as "not investigated further."
`race_test.go` and `scope_guard_test.go` (the two Go test files under
`services/race_engine/`) were each touched in exactly one commit,
`0c9b4df` (Week 0), and are byte-identical from that commit through the
current HEAD (`7905292`) — confirmed by direct diff, zero differences,
so no later commit could have added or removed a test either. Counted
at `0c9b4df` directly: `race_test.go` has 7 `Test` functions,
`scope_guard_test.go` has 9 — 16 total, matching the actual current
`go test -v ./...` output exactly (also 16). Commit `c9ee6f7`'s message
("Go race_engine + smuggling_engine still 15+16 passing") is the source
of the "15" figure; confirmed via `git show --stat c9ee6f7` that this
commit touched zero files under `services/` — it was a pure Python fix
(keyring backend error handling) whose message asserted the Go counts
were unchanged by it ("still 15+16"), not a freshly-measured count. The
figure was wrong (or already stale) at the time that message was
written; it was never corrected because commit messages here aren't
rewritten after the fact, and nothing downstream ever depended on the
prose in an old commit message being accurate. No code or test change
follows from this — the real count was always 16, and always has been.

## 31. `core/governance/scope_enforcer.py` — no Section 12 week assignment anywhere; built now, following the SAME already-established policy as items 4 and 9

Found while implementing item 28's resolution: `browser_tool.py`'s
Section 3 scope-check skeleton calls `scope_enforcer.is_allowed(url)`,
but `scope_enforcer.py` did not exist anywhere in this repository, and —
separately from the Week 3-vs-6 conflict already resolved in item 28 —
grepping every `scope_enforcer` mention in the blueprint (Sections 2, 3,
4.4, 7.28, 10.1, 10.2, 14, 16) turns up zero explicit week assignment
for *creating* the file itself. Week 6's row only ever *adds*
`credential_validation_allowlist` "in `scope_enforcer.py`," presupposing
the file already exists by then.

**This is precedent-following, not a novel judgment call.** It is the
exact same gap class already settled twice in this log: item 4
(`BudgetProfile` — "no week explicitly names this in Section 12... It
will land whenever `config.py`/`AgentConfig` is first substantively
built, or no later than Week 7 if that comes first") and item 9
(`token_throttler.py` — "Grep-verified: zero hits inside Section 12's
build-order table... Same shape as item 4's `BudgetProfile` gap,
resolved the same way: identify the actual first consumer, land it
there"). The standing policy those two entries already established:
*content fully specified elsewhere + missing week tag + a real, current
consumer* → build it against its first consumer, cite the gap, move on.
`scope_enforcer.py` satisfies all three exactly as `token_throttler.py`
did: Section 4.4 gives its logic verbatim (fully specified), Section 12
never assigns it a week (missing tag), and `browser_tool.py` — a live
Week 3 deliverable — is its first real consumer. Distinguished
explicitly (see item 32 below, corrected) from a DIFFERENT gap shape
this same session initially conflated it with: content that is *not*
specified anywhere, which items 4/9's policy was never meant to cover
and does not apply to.

Same gap shape as `approval_manager.py`/`telegram_bot.py`
(`safety_gate.py`'s own docstring; this file's `IntentEngine`
discussion) in the sense of "referenced everywhere, owned nowhere in
Section 12" — but handled differently, deliberately: those are stubbed
behind a fail-closed `Protocol` because the real thing is an
asynchronous, human-mediated round trip that cannot be partially built.
Scope checking has no such property: Section 4.4 gives the complete,
deterministic wildcard-matching logic verbatim, and a fail-closed stub
here would make `browser_tool.py` permanently non-functional rather
than failing closed on a rare path.

**Built:** `_is_scope_allowed` and `is_allowed(url, scope_domains, *,
caller_id=None)`, transcribed from Section 4.4 (v6.5/V6.4-M2 fix)
exactly, including the explicit `caller_id` parameter for future Week 6
use. Reuses `scope_config_generator.load_scope_domains` for reading
`configs/scope.yaml` rather than adding a second YAML parser (that
module's own docstring: "the single place that knows how to read
configs/scope.yaml").

**Deliberately NOT built:** `is_allowed_outbound`/`METADATA_HOSTS`/the
`.interactsh.com` exception (Section 4.4 titles that code block "Python
HTTP layer" specifically; `browser_tool.py`'s own skeleton never calls
it; `intercepting_client.py` — the actual consumer — is Week 5). The
`credential_validation_allowlist` exemption branch inside `is_allowed`
(explicitly Week 6, Section 12). Wiring into `RateLimitedClient` (Week
5) or `call_target()` (Week 6). 17 tests, real `configs/scope.yaml`
exercised directly in one of them (not fixture-only).

## 32. `BrowserCapture` — CORRECTED: this was a (B)-shaped gap (content unspecified) wrongly given (C)-shaped treatment (missing week tag only); fields now PROVISIONAL, not committed

**Self-correction, flagged by the project owner and independently
re-confirmed here, not disputed.** The original version of this entry
argued `BrowserCapture` could be built now, minimally, because its
"blast radius" was smaller than `MentalModel`'s. That reasoning
conflated two genuinely different situations: `scope_enforcer.py`
(item 31) and `token_throttler.py` (item 9) are missing ONLY a week
number — their actual content is given verbatim elsewhere in the
blueprint. `BrowserCapture`, like `MentalModel`, is missing the content
itself: no field list exists anywhere in the blueprint (grep-confirmed,
one bare type-annotation mention total), which is the same category of
gap `ExploitCandidate`/`PoC` got deferred entirely for in Week 1 — and
which this same session correctly withheld from committed code for
`MentalModel` two entries above, but inconsistently did not withhold
for itself here. "Fewer consumers this week" was true but irrelevant:
it affects blast radius if the guess is wrong, not whether a guess is
being made. It was.

**Corrected status: PROVISIONAL**, per rule (B) — proposed with cited
grounding, marked unmistakably, built on top of because blocking is
worse than a marked guess, but not treated as settled.

**Confirmed field spec (this reply), replacing the original field
list:** `requested_url: str`, `final_url: str`, `html: str`,
`status_code: int | None` — same shape as originally written, renamed
`status` → `status_code` for clarity, now standing on an actual
citation rather than the field-list author's own judgment: kept
deliberately minimal because `JSAnalysisResult` (Section 3,
`surface.py`) already owns structured JS-specific findings (endpoints,
secrets, DOM sinks, frameworks) — `BrowserCapture` is only the raw page
fetch feeding into it and into `MentalModelBuilder`'s parsing, not a
second place for those same findings to live.

`core/ontology/browser.py` and its docstring updated accordingly:
`status` renamed to `status_code`; module and class docstrings now say
PROVISIONAL explicitly, with the corrected reasoning above rather than
the original blast-radius argument. Tests updated to match the renamed
field.

## 33. Section 3 vs. Section 6.3 — `mental_model/` Groq-call numbering reconciled as a bookkeeping mismatch, not a hard-stop contradiction

**The mismatch, independently re-verified against both cited sections
directly (not accepted from characterization alone):** Section 6.3
numbers four Groq calls: (1) business purpose + roles + data flows, (2)
trust boundaries, (3) developer assumptions, (4) exploitability
scoring. Section 3's `mental_model/` tree instead labels
`boundary_identifier.py` "Groq call 1: trust boundary synthesis" —
Section 6.3's call *(2)*, not *(1)* — and no file anywhere in Section
3's tree is named for "business purpose + roles + data flows" at all
(`role_mapper.py`/`flow_tracer.py` are both explicitly "Local 7B,
recon_architect persona," not Groq, per their own Section 3 comments).
`assumption_extractor.py` is Section 3's "call 2" (developer
assumptions — Section 6.3's call *(3)*), and `exploitability_scorer.py`
is Section 3's "calls 3+4" (Section 6.3's call *(4)* alone).

**Why this is reconciled, not a hard stop:** both sections agree on the
one number that actually matters for a budget/scope decision — exactly
4 total Groq calls for `MentalModelBuilder` (Section 6.3 explicitly;
Section 8.4's "MentalModelBuilder: 4 (`groq_strategy_model`)"; Section
9.3's table, same figure) — and roughly agree on the four categories of
content those calls must produce collectively. The disagreement is
which FILE's label corresponds to which NUMBER, not two incompatible
values for the same field (contrast item 28's genuine Week-3-vs-6
double-booking, or item 4's real 3-vs-4 `BusinessValue` member-count
conflict) — nothing here would come out wrong if built either way,
because nothing yet consumes a specific "call N" label as data.

**Working reading, adopted for planning purposes, not asserted as
blueprint-cited fact:** `boundary_identifier.py`'s single Groq call
produces BOTH business-purpose/roles/data-flows AND trust boundaries in
one structured response — Section 3's comment names only the headline
output (trust boundaries), not everything that call's response
actually contains. This accounts for the otherwise-unassigned "business
purpose + roles + data flows" content without inventing a fourth
mental_model/ file Section 3 never lists, and keeps the total at
exactly 1 (`boundary_identifier`) + 1 (`assumption_extractor`) + 2
(`exploitability_scorer`, "calls 3+4") = 4, matching every cross-section
total. Flagged as a reading, not a fact, because nothing in either
section explicitly confirms one call's response covers both topics —
if built, `boundary_identifier.py`'s response schema should be designed
against this reading explicitly, and revisited if that turns out wrong
once real Groq-calling code exists.

Does not block anything built this session: none of `target_adapter.py`,
`campaign_planner.py`, or `info_gain_scorer.py` depend on this numbering,
and the six actual Groq/local-7B-calling files
(`role_mapper.py`/`flow_tracer.py`/`boundary_identifier.py`/
`assumption_extractor.py`/`exploitability_scorer.py`/`builder.py`) are
not built this session either — see the Week 3 completion report for
why (a materially larger, separate gap: no `config.py`, no established
Groq/Ollama HTTP-calling pattern anywhere in this repository yet, and no
prompt/response-schema specification anywhere in the blueprint for any
of the four calls' actual content).

## 34. `core/planning/target_adapter.py` — PROVISIONAL: interface built, weight-adjustment logic deliberately inert

Section 6.4 gives exactly one sentence for this component ("uses
`MentalModel` + `TargetAdapter` (`TargetType` enum)... ordered test plan
and scanner weights") — no method signature, no per-`TargetType`
adjustment values, no algorithm anywhere in the blueprint's 16 sections
(`TargetAdapter` appears at this one location, grep-confirmed). This is
a (B)-shaped gap (content genuinely unspecified), the same category as
`MentalModel` before item 29's confirmation and `BrowserCapture` after
item 32's correction — not a (C)-shaped one like `scope_enforcer.py`
(item 31): there is no citable content to transcribe here, only a
role description.

**Built, marked PROVISIONAL:** `TargetAdapter` holds a `target_type:
TargetType` (defaulting to `UNKNOWN`) and exposes
`adjust_weights(base_weights) -> dict`. `adjust_weights` is the
identity function for every `TargetType`, including `UNKNOWN` —
deliberately, not as a placeholder oversight: no blueprint section
gives a single concrete per-type weight adjustment for any of the 7
`TargetType` members, and inventing a 7×29 adjustment table with zero
citation would be exactly the magic-number fabrication the Engineering
Constitution's config-driven rule exists to prevent, doubly so for
BEHAVIORAL weights rather than a formatting choice. 6 tests, all
pinning the current identity behavior explicitly (so a future real
implementation changes these tests deliberately, not by silent
regression) — including one confirming identity holds for all 7
`TargetType` members, not just a couple of examples.

**Also out of scope, flagged rather than silently skipped:** how a
target actually GETS assigned a `TargetType` from recon signals in the
first place. `ReconState` (Section 3, `core/ontology/state.py`) does
not exist yet in this repository — there is nothing yet to classify
FROM. Callers construct `TargetAdapter` with an already-known
`TargetType`.

## 35. `core/planning/campaign_planner.py` — PROVISIONAL: `CampaignPlan` output type invented locally (not ontology), ordering rule invented and flagged

**Same (B)-shaped gap as item 34**, one level up: Section 6.4's single
sentence never names a field list for its "ordered test plan and
scanner weights" output. This session's own name for that output,
`CampaignPlan`, is not blueprint-cited; Section 8.1's integration-flow
line separately calls the same conceptual hand-off "`ReconConfig`"
("CampaignPlanner -> ReconSubgraph: `ReconConfig`") — a different name,
also with no field list given anywhere, so not treated as a rule-(A)
contradiction (nothing asserts two incompatible VALUES for one field;
there are just two informal names for a thing nothing downstream
consumes yet, since `ReconSubgraph` doesn't exist either). Reconcile
the naming if/when a real `ReconSubgraph` consumer is built and needs a
specific shape — against that consumer's actual needs, not guessed now.

**Placement, deliberately different from `MentalModel`/`BrowserCapture`:**
`CampaignPlan` lives in `core/planning/campaign_planner.py` itself, NOT
`core/ontology/`. Distinguished explicitly rather than repeating item
32's original inconsistency under a new name: `MentalModel` is
explicitly a `ParentState` field per Section 3's own `state.py` comment,
and `BrowserCapture` is at least a named return-type annotation in a
comment block — both have SOME textual tie to `core/ontology/`.
`CampaignPlan`/`ReconConfig` has none; its only cited consumer
(`ReconSubgraph`) is single and doesn't exist yet — the same
low-blast-radius, single-file shape Week 2 used for
`WebhookEvent`/`PersonaName` (items 21/23), which also live locally, not
in `core/ontology/`.

**Ordering algorithm — PROVISIONAL, invented, flagged:** "ordered test
plan" is read as "scanners ordered by `TargetAdapter`-adjusted weight,
descending" — the simplest reading consistent with the one cited
sentence. `MentalModel` is accepted as a required parameter (matching
"uses `MentalModel` + `TargetAdapter`" literally) and carried on the
returned `CampaignPlan`, but this week's ordering logic does not read
any of its fields — no cited rule says HOW `MentalModel` content should
influence order, only that the component uses it. A plausible-sounding
rule (e.g. "boost scanners relevant to identified trust boundaries")
was deliberately not invented — that's exactly the kind of guess rule
(B) exists to prevent. 4 tests, including one pinning that identity
weights (this week's `TargetAdapter` behavior) flow through unchanged.

## 36. `core/planning/info_gain_scorer.py` — FULLY SPECIFIED, not provisional; Section 6.7's exact formula

**Different category from items 34/35, confirmed before writing any
code, not after:** Section 6.7 gives an exact, complete formula —
"HIGH if max(exploitability_score of relevant assumptions) >= 0.7;
MEDIUM if 0.4 <= max < 0.7; LOW if max < 0.4" — a (C)-shaped situation
(content fully specified) even though, like items 34/35, no explicit
week number ties `info_gain_scorer.py` to Week 3 beyond the Week 3 row
itself naming it directly ("`BusinessValue` in `info_gain_scorer.py`").
Built as settled, non-provisional code accordingly — no PROVISIONAL
marking anywhere in this module, deliberately, since there is nothing
guessed in the threshold logic itself.

**One interpretive choice, documented rather than silently resolved:**
Section 6.7 never defines what makes an assumption "relevant" (e.g.
relevant to a specific future `BeliefGraph` hypothesis, Week 4).
Resolved by NOT resolving it here: `score_assumptions()` takes an
already-filtered `list[Assumption]` and the CALLER decides what
"relevant" means for their context; `score_mental_model()` is this
week's own simplest reading (score every assumption in the given
`MentalModel`, since no `BeliefGraph` exists yet to narrow the set).
This sidesteps inventing a relevance-filter algorithm entirely, rather
than guessing one.

**Also decided, cited exactly:** `BusinessValue.UNKNOWN` (item 27) is
excluded from this function's return values by construction — no
threshold branch produces it; a dedicated test
(`test_never_returns_unknown`) pins this across the full score range.
Empty `assumptions` raises `ValueError` rather than silently defaulting
to a threshold Section 6.7 never names for that case. 12 tests,
including exact boundary values (0.4 and 0.7 themselves, not just
interior examples either side) — this project's own standard for
threshold arithmetic, not eyeballed.

## 37. The six `core/mental_model/` Groq/local-7B-calling files — NOT built; a materially different, larger gap than items 34/35, not attempted as "provisional"

**Not `role_mapper.py`, `flow_tracer.py`, `boundary_identifier.py`,
`assumption_extractor.py`, `exploitability_scorer.py`, or `builder.py`.**
Considered directly, not skipped by omission: implementing any of these
for real requires infrastructure this repository does not have yet, at
a scale beyond a single field list or weight table:

1. **No `config.py`/`AgentConfig`** (Section 3: "`config.py` # AgentConfig
   + all Enums + BudgetProfile") exists anywhere in this repository —
   confirmed by directory listing. There is no structured place to read
   `groq_strategy_model`'s live-verified model ID from at runtime (only
   `scripts/verify_groq_models.py`'s own narrow, Week-0 preflight
   purpose reads `configs/llm_config.yaml` directly, for a different
   job: checking the ID is live, not making chat-completion calls with
   it).
2. **No established Groq or Ollama HTTP-calling pattern exists anywhere
   in this repository.** `scripts/verify_groq_models.py` and
   `verify_gemini_models.py` call `GET /models`-style endpoints for
   liveness checks only — neither is a template for an actual chat/
   completion call, request/response shape, retry policy, or error
   mapping to `FailureCause` (Section 8.3).
3. **No prompt content or response schema is specified anywhere in the
   blueprint** for any of the four Groq calls (business purpose+roles+
   data-flows synthesis; trust-boundary identification; assumption
   extraction; exploitability scoring) or for the two "Local 7B,
   recon_architect persona" calls (`role_mapper.py`, `flow_tracer.py`).
   Section 6.3 names WHAT each call produces at a category level: it
   does not specify HOW to ask a model for it or what shape to parse
   back.

**Why this is not treated as rule (B) ("write a provisional proposal,
mark it, keep building")**, unlike items 34/35: a provisional version
of `TargetAdapter`/`CampaignPlan` was a small, bounded, reasoned
inference from an actual cited hint (Section 2.5's Context Window
placeholders, in `MentalModel`'s case; a single descriptive sentence,
in `TargetAdapter`/`CampaignPlan`'s). A "provisional" `boundary_identifier.py`
would instead require inventing, from nothing citable: the literal
prompt text sent to Groq, the exact JSON/structured-output schema
expected back, retry/timeout/rate-limit handling wired to
`token_throttler.py` (built Week 1, sitting unused for exactly this
reason — item 9), and the actual runtime source of a live model ID
(`config.py`, which also doesn't exist). That is not "a reasoned
inference from a citable hint marked as a guess" — it is writing the
entire component from nothing, four to six times over, and marking the
result "provisional" would not change that almost none of it traces to
the blueprint. Stopping here and naming exactly what's missing is the
correct call for this shape of gap, not a shortfall against items
34-36's bar.

**What WOULD unblock this:** either (a) explicit design input on prompt
content and response schema for each of the four Groq calls plus the
two local-7B calls, or (b) authorization to design `config.py` and a
minimal Groq/Ollama HTTP client abstraction from scratch as
prerequisite infrastructure — itself a substantially larger, riskier
piece of net-new design than anything built this session (items
31/34/35's prerequisite-building was each a single, already-fully- or
mostly-specified function; this would be original infrastructure design
with no equivalent blueprint citation to transcribe against). Flagged
here rather than either silently attempted or silently dropped.

