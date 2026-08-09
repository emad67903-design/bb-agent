"""
Implements: Section 3 -- core/sandbox/result_parser.py (Section 3 gives
this file no descriptive comment at all, unlike its three siblings --
its responsibility is inferred from Section 10.7's overall shape:
something has to turn a raw subprocess/validator outcome into the
structured result the rest of the system consumes, and no other
Section-10.7 file claims that job).
Blueprint: bb_agent_v6.6_final_blueprint.md

REUSES `core.ontology.sandbox.SandboxOutcome`, DOES NOT INVENT A SECOND
CLASSIFICATION SCHEME: `code_executor.py` is the only place that can
actually observe which of the four outcomes happened (validator
rejection before any subprocess exists; a subprocess that ran to
completion, however the script itself fared; one that was still alive
past the timeout; one that hit the memory ceiling) -- classification
happens there, at the point of observation, not here. This module's job
is narrower and purely downstream: given an already-classified outcome
plus its raw pieces (captured stdout, an exception's formatted text, how
long it ran), assemble the one `SandboxExecutionResult` shape Section
10.7's four files collectively imply, consistently, in one place, so
`code_executor.py` itself does not need four slightly-different, hand-
rolled dataclass constructions scattered through its own subprocess-
handling code.

STDOUT TRUNCATION: not blueprint-specified, a small addition of this
file's own. The 128 MB memory ceiling already bounds how much a script
COULD print (an in-memory `io.StringIO` buffer counts against that same
limit), but nothing stops a script from printing exactly up to that
ceiling in a single call, which would be an awkward, near-arbitrarily-
large string to hand an LLM caller as "the PoC's output." Capped at
`STDOUT_PREVIEW_CHARS` for the same reason `call_target`'s own
`body_preview` is capped at 512 chars (Section 10.7) -- a bounded
preview is more useful to a downstream LLM/report than an unbounded
blob, and the cap is far above what a well-behaved verification script
would ever need to print to prove a finding.
"""

from __future__ import annotations

from core.ontology.sandbox import SandboxExecutionResult, SandboxOutcome
from core.sandbox.sandbox_validator import ValidationResult

STDOUT_PREVIEW_CHARS = 8_192


def _truncate_stdout(stdout: str) -> str:
    """Caps `stdout` at `STDOUT_PREVIEW_CHARS`, appending a fixed marker
    if truncation occurred so a caller can tell full output from
    truncated output without comparing lengths against the constant
    itself."""
    if len(stdout) <= STDOUT_PREVIEW_CHARS:
        return stdout
    return stdout[:STDOUT_PREVIEW_CHARS] + "\n...[truncated]"


def build_rejected_result(validation: ValidationResult) -> SandboxExecutionResult:
    """Section 10.7: "AST blocklist (immediate rejection)" -- the code
    never ran, no subprocess was ever spawned.

    Args:
        validation: The failing `sandbox_validator.validate_code()`
            result (`code_executor.py` is expected to call this only
            when `validation.is_valid` is `False` -- calling it with a
            passing result would produce a `REJECTED_BY_VALIDATOR`
            result with an empty `rejection_reasons` list, which is
            self-contradictory; not guarded against here defensively,
            since the one caller this module has controls that
            invariant directly).

    Returns:
        A `SandboxExecutionResult` with `outcome=REJECTED_BY_VALIDATOR`,
        empty `stdout`, `execution_seconds=None`, and `rejection_reasons`
        copied from `validation.violations`.
    """
    return SandboxExecutionResult(
        outcome=SandboxOutcome.REJECTED_BY_VALIDATOR,
        stdout="",
        error_message="; ".join(validation.violations),
        rejection_reasons=list(validation.violations),
        execution_seconds=None,
    )


def build_completed_result(
    *,
    stdout: str,
    exception_text: str | None,
    execution_seconds: float,
) -> SandboxExecutionResult:
    """The subprocess ran to completion within the timeout and memory
    limit -- Section 10.7's normal case, whether or not the script's own
    logic raised.

    Args:
        stdout: Everything the script printed.
        exception_text: `None` if the script ran without raising.
            Otherwise the formatted exception (e.g. from
            `code_executor.py`'s own `f"{type(exc).__name__}: {exc}"`) --
            covers both a bug in the script itself and
            `safety_guard.SandboxOutOfScopeError` from a rejected
            `call_target()` call; both are "the script ran, and then it
            raised," not a resource-limit failure.
        execution_seconds: Wall-clock time the subprocess ran.

    Returns:
        A `SandboxExecutionResult` with `outcome=COMPLETED`.
    """
    return SandboxExecutionResult(
        outcome=SandboxOutcome.COMPLETED,
        stdout=_truncate_stdout(stdout),
        error_message=exception_text,
        rejection_reasons=[],
        execution_seconds=execution_seconds,
    )


def build_timed_out_result(*, stdout: str, execution_seconds: float, timeout_seconds: float) -> SandboxExecutionResult:
    """The subprocess was still alive after `timeout_seconds` and was
    terminated.

    Args:
        stdout: Whatever `code_executor.py` was able to recover before
            the process was killed -- empty in the common case (a
            genuinely hung script is killed by SIGTERM before it ever
            reports anything back, confirmed empirically in that
            module's own tests), populated only in the narrow race where
            the child had already finished reporting by the moment
            termination was requested.
        execution_seconds: Wall-clock time from spawn to termination
            (approximately `timeout_seconds`, by construction).
        timeout_seconds: The limit that was exceeded, for the message
            text -- passed in rather than imported from
            `code_executor.py`, to avoid a circular import between the
            two modules (that module imports this one to build its
            results).

    Returns:
        A `SandboxExecutionResult` with `outcome=TIMED_OUT`.
    """
    return SandboxExecutionResult(
        outcome=SandboxOutcome.TIMED_OUT,
        stdout=_truncate_stdout(stdout),
        error_message=f"script exceeded the {timeout_seconds:g}s execution limit and was terminated",
        rejection_reasons=[],
        execution_seconds=execution_seconds,
    )


def build_memory_exceeded_result(
    *,
    stdout: str,
    execution_seconds: float,
    memory_limit_bytes: int,
) -> SandboxExecutionResult:
    """The subprocess hit its RAM ceiling -- either the script's own
    `MemoryError` propagated out (the common case under `RLIMIT_AS`) or
    the OS killed the process outright.

    Args:
        stdout: Whatever the script had printed before the limit was
            hit.
        execution_seconds: Wall-clock time from spawn to the limit being
            hit.
        memory_limit_bytes: The limit that was exceeded, for the message
            text -- same circular-import reasoning as
            `build_timed_out_result`'s `timeout_seconds` parameter.

    Returns:
        A `SandboxExecutionResult` with `outcome=MEMORY_EXCEEDED`.
    """
    limit_mb = memory_limit_bytes / (1024 * 1024)
    return SandboxExecutionResult(
        outcome=SandboxOutcome.MEMORY_EXCEEDED,
        stdout=_truncate_stdout(stdout),
        error_message=f"script exceeded the {limit_mb:g} MB memory limit",
        rejection_reasons=[],
        execution_seconds=execution_seconds,
    )
