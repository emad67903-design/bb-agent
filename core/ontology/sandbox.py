"""
Implements: Section 10.7 -- the sandbox's execution-outcome shape.
Blueprint: bb_agent_v6.6_final_blueprint.md

Section 10.7 names four files (`code_executor.py`, `result_parser.py`,
`safety_guard.py`, `sandbox_validator.py`) but no result *type* for what
`code_executor.py` produces and `result_parser.py` consumes/refines. Per
the Engineering Constitution ("Every dataclass... is defined ONCE, in
core/ontology/"), that type belongs here, not invented inline in either
file. New file (`sandbox.py`) rather than an addition to an existing
one, for the same reason `core/ontology/scope.py` was new this week:
nothing existing owns this domain (`findings.py` owns confirmed
vulnerabilities/PoCs post-verification, a different and later concept
than "did this arbitrary script run cleanly").

`SandboxOutcome` is intentionally its own small enum here, not folded
into `core/ontology/enums.py`'s `FailureCause` -- `FailureCause` (Section
8.3's system-wide fallback-cascade vocabulary: NO_SIGNAL, WAF_BLOCKED,
BUDGET_EXHAUSTED, etc.) does not exist in this codebase yet (grep-
confirmed: `core/ontology/enums.py` lists it only in a forward-looking
comment, Weeks 0-5 never built it), and reaching into an unbuilt,
system-wide enum to borrow a couple of loosely-related members for one
component's narrow, specific outcome vocabulary would be scope creep in
the wrong direction -- inventing FailureCause's real membership now, for
a purpose Section 8.3 never assigned it, is a bigger unstated decision
than this file needs to make.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SandboxOutcome(str, Enum):
    """How one `code_executor.py` run of an LLM-generated PoC script
    ended. Exactly one of these per `SandboxExecutionResult`.

    REJECTED_BY_VALIDATOR: `sandbox_validator.py`'s AST check rejected
        the code before any subprocess was ever spawned (Section 10.7:
        "AST blocklist (immediate rejection)").
    COMPLETED: The subprocess ran the code to completion (with or
        without the code itself raising an exception -- see
        `SandboxExecutionResult.error_message`) within the timeout and
        memory limit.
    TIMED_OUT: The subprocess was still running after
        `code_executor.SANDBOX_TIMEOUT_SECONDS` and was terminated.
    MEMORY_EXCEEDED: The subprocess hit `code_executor.SANDBOX_MEMORY_LIMIT_BYTES`
        (Section 10.7: "128 MB RAM") and was stopped -- either because
        the untrusted code's own `MemoryError` propagated out (the
        common case: Python's allocator raises this itself when
        `RLIMIT_AS` is hit) or because the OS killed the process
        outright (rarer, but possible depending on what allocation
        attempted to exceed the limit).
    """

    REJECTED_BY_VALIDATOR = "rejected_by_validator"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    MEMORY_EXCEEDED = "memory_exceeded"


@dataclass(frozen=True)
class SandboxExecutionResult:
    """The complete, structured outcome of one sandboxed PoC execution.

    Attributes:
        outcome: Which of the four `SandboxOutcome` categories this run
            ended in.
        stdout: Everything the script printed via `print()` -- its only
            permitted output channel (Section 10.7: "open: ALL modes
            blocked; output via print() only"). Empty string if nothing
            was printed, if the code was rejected before running, or if
            it crashed before its first print.
        error_message: `None` on a clean run. Otherwise: the validator's
            rejection reason(s) joined into one string (`REJECTED_BY_VALIDATOR`),
            the formatted exception the script itself raised or that
            `call_target()` raised on an out-of-scope URL (`COMPLETED`
            with a script-level error), or a fixed explanatory message
            for `TIMED_OUT` / `MEMORY_EXCEEDED`.
        rejection_reasons: The validator's specific findings (e.g.
            `"blocked import: socket"`), one entry per distinct
            violation found, in source order. Always empty except for
            `REJECTED_BY_VALIDATOR` -- kept as a list, not folded into
            `error_message` alone, so a caller can act on individual
            violations (e.g. count them, or show them in a UI) without
            re-parsing a joined string.
        execution_seconds: Wall-clock time the subprocess actually ran,
            from spawn to the result being collected. `None` for
            `REJECTED_BY_VALIDATOR` (no subprocess was ever spawned).
    """

    outcome: SandboxOutcome
    stdout: str
    error_message: str | None
    rejection_reasons: list[str]
    execution_seconds: float | None
