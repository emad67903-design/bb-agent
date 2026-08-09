"""
Implements: Section 3 -- core/sandbox/code_executor.py ("LLM-generated
Python PoC only; 30s timeout; 128 MB RAM"), and Section 10.7's
"Executes: LLM-generated Python PoC verification scripts only.
Pre-written scanners run in the main process."
Blueprint: bb_agent_v6.6_final_blueprint.md

THE MECHANISM BELOW IS A TRANSCRIPTION OF AN ALREADY-PROVEN PATTERN, NOT
A NEW DESIGN: every piece here (fork via `multiprocessing.Process`,
`resource.setrlimit(RLIMIT_AS, ...)` set inside the child before
executing untrusted code, `join(timeout)` then `terminate()` then
`kill()` escalation, stdout captured via redirection to an in-process
buffer and returned over a `multiprocessing.Pipe`) was individually
verified empirically against this exact sandboxed environment before
being written here -- not assumed to work from general Python
knowledge. Four things specifically needed verification, not just
reasoning, because subprocess/resource-limit behavior is notoriously
environment-dependent:
  1. `RLIMIT_AS=128MB` is viable at all in a forked child that has
     already imported `httpx`/`asyncio` (a real risk: CPython's own
     interpreter overhead can exceed small RLIMIT_AS ceilings before
     any untrusted code even runs) -- confirmed viable (baseline ~35MB
     VSZ with the actual import set this module needs).
  2. `RLIMIT_AS` has real teeth (a 300MB allocation attempt under a
     128MB cap raises `MemoryError`, not silently succeeds) -- confirmed.
  3. `join(timeout)` + `terminate()` (SIGTERM) + `kill()` (SIGKILL)
     escalation actually stops a hung child -- confirmed. `kill()` is
     necessary, not `terminate()` alone: `signal` is not in Section
     10.7's blocklist or this file's own hardening additions (blocking
     it would not prevent anything a `terminate()`-then-`kill()`
     escalation doesn't already handle, since SIGKILL cannot be caught
     or ignored by any Python code, sandboxed or not), so a script that
     installs its own SIGTERM handler must not be assumed to die from
     `terminate()` alone.
  4. `asyncio.run()` (needed inside `call_target`, Section 10.7's
     synchronous-signature pre-approved function, to bridge to the
     async `RateLimitedClient`) works correctly inside an
     `RLIMIT_AS`-constrained child -- confirmed.

RESOURCE LIMIT: `SANDBOX_MEMORY_LIMIT_BYTES` USES `RLIMIT_AS`
(virtual address space), NOT `RLIMIT_DATA` OR RSS-POLLING: `RLIMIT_AS`
is the standard, OS-enforced (not polled) mechanism for this and was the
one empirically verified above. `RLIMIT_DATA` does not reliably cover
`mmap`-backed allocations modern allocators use for larger objects;
RSS-polling from the parent would leave a real window where a spike
completes before being observed and killed. `RLIMIT_AS` has neither
gap, at the cost of also counting reserved-but-unwritten virtual memory
-- a real, known trade-off, not treated as free.

WHY THE RESULT TRAVELS BACK VIA A `multiprocessing.Pipe()`, NOT A
`multiprocessing.Queue()`: `multiprocessing.Process`'s target function's
return value is discarded -- there is no built-in way to get a value
back from a forked child except an explicit IPC channel. `Queue` was
the first choice (it is what this file's own design smoke tests used)
but was replaced after a concrete failure, not preference: `Queue.put()`
lazily starts an internal feeder thread on its FIRST call, and starting
a thread requires its own stack allocation (several MB, platform-
dependent) -- which itself can fail with `RuntimeError: can't start new
thread` under the EXACT `RLIMIT_AS` memory pressure the sandbox most
needs to report through the queue (reproduced directly: a script that
allocates past the limit correctly raised `MemoryError` inside
`_sandboxed_worker`, and the subsequent `result_queue.put(payload)` --
and its own fallback `except` clause's `put(...)` -- BOTH then failed
with that `RuntimeError`, visible on the child's stderr, because neither
had enough remaining headroom under the same RLIMIT_AS ceiling to spin
up Queue's feeder thread for the first time at exactly the moment it was
needed). `Pipe.send()` has no equivalent internal thread -- it performs a
direct, synchronous write to the underlying OS pipe -- and is besides a
better fit for this file's actual use (exactly one message, one
producer, one consumer; `Queue`'s multi-producer thread-safety machinery
was never needed here). Fixed and reverified empirically before being
written this way, the same standard as the four numbered items above.

`os`/`subprocess`/`sys`/... IMPORTED HERE, IN THE HARNESS, ARE NOT
GOVERNED BY `sandbox_validator.py`'s BLOCKLIST: that blocklist statically
analyzes the UNTRUSTED code string only. This file is trusted,
implementer-written harness code -- exactly like `safety_guard.py`
importing `httpx`/`socket` directly (that module's own docstring makes
the same point). `sandbox_validator.BLOCKED_MODULES` governs what the
STRING passed to `execute_sandboxed_poc` may reference, never what this
file itself may import.
"""

from __future__ import annotations

import io
import multiprocessing
import resource
import sys
import time
import traceback

from core.ontology.sandbox import SandboxExecutionResult
from core.sandbox import result_parser
from core.sandbox.sandbox_validator import build_restricted_globals, validate_code

# Section 3's file-tree comment for this file, verbatim: "30s timeout;
# 128 MB RAM". No YAML config file claims these (neither exists yet;
# core/config.py is unbuilt as of this week, and no vuln_thresholds.yaml/
# vuln_weights.yaml/scope.yaml/llm_config.yaml key is a natural home for
# a sandbox execution limit) -- following the established precedent for
# exactly this situation (`core/http/intercepting_client.py`'s
# `BODY_CAP_BYTES`/`ENTRY_CAP`, cited to their own blueprint section as
# bare module constants, not stuffed into an unrelated YAML file), these
# are cited module constants, not magic numbers.
SANDBOX_TIMEOUT_SECONDS: float = 30.0
SANDBOX_MEMORY_LIMIT_BYTES: int = 128 * 1024 * 1024

# Grace period given to a terminated (SIGTERM) process before escalating
# to kill() (SIGKILL). Not blueprint-specified -- standard subprocess-
# reaping practice, kept short since Section 10.7's own timeout is
# already the primary bound a caller waits on; this is only how much
# LONGER the parent waits past that bound before guaranteeing the child
# is gone.
_TERMINATE_GRACE_SECONDS: float = 2.0


def _sandboxed_worker(
    code: str,
    scope_domains: list[str],
    transport: object | None,
    child_conn: multiprocessing.connection.Connection,
) -> None:
    """Runs INSIDE the forked child only -- never called directly by
    `execute_sandboxed_poc` in the parent process.

    Sets the RAM ceiling, builds the restricted execution namespace,
    `exec()`s the (already-validated, by the parent, before this
    process was even spawned) code, captures everything it prints, and
    sends exactly one raw outcome dict over `child_conn`. Never lets an
    exception escape this function itself -- an uncaught exception here
    would just look like an unexplained process death to the parent,
    indistinguishable from a timeout or an OOM kill; every failure mode
    this function can encounter is instead caught and reported through
    the pipe.

    Args:
        code: The LLM-generated Python source. Assumed already validated
            by the caller -- this function does not re-validate, since
            re-parsing here would just duplicate `sandbox_validator`'s
            own work inside the resource-constrained child for no
            benefit.
        scope_domains: The program's in-scope domain patterns, passed to
            `build_restricted_globals` unchanged.
        transport: Passed to `build_restricted_globals` unchanged --
            `None` for real network I/O in production use, an
            `httpx.MockTransport` for integration tests.
        child_conn: This process's end of a `multiprocessing.Pipe()`.
            Exactly one dict is sent before this function returns:
            `{"stdout": str, "exception_text": str | None,
            "is_memory_error": bool}`. `Pipe`, not `Queue` -- module
            docstring explains why the switch was necessary, not
            stylistic.
    """
    resource.setrlimit(
        resource.RLIMIT_AS,
        (SANDBOX_MEMORY_LIMIT_BYTES, SANDBOX_MEMORY_LIMIT_BYTES),
    )

    stdout_buffer = io.StringIO()
    exception_text: str | None = None
    is_memory_error = False

    original_stdout = sys.stdout
    try:
        restricted_globals = build_restricted_globals(scope_domains, transport=transport)
        sys.stdout = stdout_buffer
        try:
            exec(compile(code, "<sandboxed_poc>", "exec"), restricted_globals)
        finally:
            sys.stdout = original_stdout
    except MemoryError as exc:
        is_memory_error = True
        exception_text = f"{type(exc).__name__}: {exc}"
    except BaseException as exc:
        # Deliberately the broadest catch in this codebase, and the one
        # place it's correct: this is the untrusted-code execution
        # boundary itself. The script may raise anything, including
        # subclasses of BaseException a plain `except Exception` would
        # not catch (e.g. a validated-but-still-present `SystemExit` --
        # `sys` is blocked from the SCRIPT's own imports, but Python's
        # own `exec()` machinery can still surface one from elsewhere in
        # rare cases). Every branch converges on "report it through the
        # pipe, do not let it propagate," so the broad catch here does
        # not hide a bug -- it is the last line of this function either
        # way.
        exception_text = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

    payload = {
        "stdout": stdout_buffer.getvalue(),
        "exception_text": exception_text,
        "is_memory_error": is_memory_error,
    }
    try:
        child_conn.send(payload)
    except Exception:
        # A send failure (e.g. something in the payload turned out to be
        # unpicklable -- should not happen for a dict of plain
        # strings/bools, but the parent must never be left waiting
        # forever on a pipe nothing was ever sent on) falls back to a
        # minimal, always-picklable payload rather than letting this
        # function raise past its own try/except.
        child_conn.send(
            {
                "stdout": "",
                "exception_text": "sandbox: failed to report the script's own outcome",
                "is_memory_error": False,
            }
        )
    finally:
        child_conn.close()


def execute_sandboxed_poc(
    code: str,
    scope_domains: list[str],
    *,
    transport: object | None = None,
    timeout_seconds: float = SANDBOX_TIMEOUT_SECONDS,
    memory_limit_bytes: int = SANDBOX_MEMORY_LIMIT_BYTES,
) -> SandboxExecutionResult:
    """The sandbox's single public entry point: validate, then execute,
    one LLM-generated PoC verification script.

    Args:
        code: The LLM-generated Python source to validate and run.
        scope_domains: The program's in-scope domain patterns, passed
            through to the sandboxed `call_target()`.
        transport: `None` for real network I/O (production use). An
            `httpx.AsyncBaseTransport` (typically `httpx.MockTransport`)
            for tests -- threaded all the way through to the real
            `call_target()` implementation, so a test exercises the
            actual composed pipeline rather than a hand-substituted
            stand-in.
        timeout_seconds: Overridable for tests only (a test proving
            `SandboxOutcome.TIMED_OUT` should not have to sleep the full
            30 seconds). Production callers should leave this at its
            `SANDBOX_TIMEOUT_SECONDS` default -- Section 3's own number.
        memory_limit_bytes: Overridable for tests only, same reasoning
            as `timeout_seconds`.

    Returns:
        A `SandboxExecutionResult`. `sandbox_validator.validate_code`
        rejecting the code produces `REJECTED_BY_VALIDATOR` without ever
        spawning a subprocess. Otherwise, exactly one of `COMPLETED`,
        `TIMED_OUT`, or `MEMORY_EXCEEDED`, per the subprocess's actual
        observed outcome.
    """
    validation = validate_code(code)
    if not validation.is_valid:
        return result_parser.build_rejected_result(validation)

    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(
        target=_sandboxed_worker,
        args=(code, scope_domains, transport, child_conn),
    )

    start_time = time.monotonic()
    process.start()
    child_conn.close()  # parent's own reference to the child's write end; the child keeps its own
    process.join(timeout=timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(timeout=_TERMINATE_GRACE_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(timeout=_TERMINATE_GRACE_SECONDS)
        execution_seconds = time.monotonic() - start_time
        stdout = _drain_partial_stdout(parent_conn)
        parent_conn.close()
        return result_parser.build_timed_out_result(
            stdout=stdout,
            execution_seconds=execution_seconds,
            timeout_seconds=timeout_seconds,
        )

    execution_seconds = time.monotonic() - start_time
    payload = _drain_payload(parent_conn)
    parent_conn.close()

    if payload is None:
        # The process ended (is_alive() is False) but sent nothing on
        # the pipe -- it was killed by the OS before its own except-
        # clauses ran (the OOM killer, or a resource-limit signal that
        # ends the process outright rather than raising a catchable
        # MemoryError inside it; empirically, RLIMIT_AS raised a
        # catchable MemoryError in every case tested, but a hard
        # OS-level kill for the same underlying cause is the documented
        # fallback SandboxOutcome.MEMORY_EXCEEDED already anticipates --
        # see that enum member's own docstring). A closed pipe with
        # nothing sent is the observable signature of this case.
        return result_parser.build_memory_exceeded_result(
            stdout="",
            execution_seconds=execution_seconds,
            memory_limit_bytes=memory_limit_bytes,
        )

    if payload["is_memory_error"]:
        return result_parser.build_memory_exceeded_result(
            stdout=payload["stdout"],
            execution_seconds=execution_seconds,
            memory_limit_bytes=memory_limit_bytes,
        )

    return result_parser.build_completed_result(
        stdout=payload["stdout"],
        exception_text=payload["exception_text"],
        execution_seconds=execution_seconds,
    )


def _drain_payload(parent_conn: multiprocessing.connection.Connection) -> dict | None:
    """Non-blocking retrieval of the child's one sent payload. `None` if
    nothing is waiting to be received -- the child ended without ever
    calling `child_conn.send(...)` (see `execute_sandboxed_poc`'s own
    comment on what this means: an OS-level kill, not a caught, reported
    error). `poll(0)` (a zero-timeout check for available data) rather
    than a blocking `recv()`, since the child process has already been
    joined by the time this is called -- if it exited without sending,
    blocking on `recv()` here would hang forever."""
    if not parent_conn.poll(0):
        return None
    try:
        return parent_conn.recv()
    except EOFError:
        return None


def _drain_partial_stdout(parent_conn: multiprocessing.connection.Connection) -> str:
    """For the narrow race where the child had already finished --
    including its own `child_conn.send(payload)` -- by the moment
    termination was requested, so its actual reported stdout should be
    used instead of an empty string.

    NOT a mechanism for recovering output from a genuinely hung script:
    confirmed empirically, not assumed, that this is the normal,
    expected case, not an edge case -- `child_conn.send(payload)` is the
    LAST line of `_sandboxed_worker`'s own top-level try/finally, and a
    script stuck in a tight loop is killed by SIGTERM (no handler
    installed) before it ever reaches that line, so its `print()`
    output -- already sitting in the child's own in-memory buffer --
    never leaves that process. `TIMED_OUT` results have empty `stdout`
    in the common case for exactly this reason, not because capture
    failed.

    Never raises; nothing waiting or a malformed payload both fall back
    to an empty string."""
    payload = _drain_payload(parent_conn)
    if payload is None:
        return ""
    return payload.get("stdout", "")
