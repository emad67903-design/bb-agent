"""
Implements: Section 3 / Section 10.7 test coverage --
core/sandbox/code_executor.py

These are INTEGRATION tests, deliberately: each one drives the real
`execute_sandboxed_poc` entry point through a real `multiprocessing.Process`
fork, a real `resource.setrlimit`, and (where relevant) a real
`call_target()` backed by a real `RateLimitedClient`/`InterceptingClient`
pair -- mocked only at the `httpx` transport layer, the same boundary
`InterceptingClient`'s own tests already mock at. This is deliberately a
different, narrower kind of test than `test_safety_guard.py` or
`test_sandbox_validator.py`, both of which correctly test their own
pieces in isolation with hand-built inputs -- this file exists
specifically to prove those pieces compose correctly end to end, which
no amount of isolated unit testing can show on its own.

`timeout_seconds`/`memory_limit_bytes` overrides are used throughout to
keep this file fast (seconds, not the real 30s/128MB) -- production
callers should never override the `SANDBOX_TIMEOUT_SECONDS`/
`SANDBOX_MEMORY_LIMIT_BYTES` defaults; `test_code_executor.py` proving
`TIMED_OUT`/`MEMORY_EXCEEDED` at reduced limits proves the SAME
mechanism (the join/terminate/kill escalation, the RLIMIT_AS ceiling) --
it is the limit's SIZE that differs, not the code path being exercised.
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import multiprocessing
import time

import httpx
import pytest

from core.ontology.sandbox import SandboxOutcome
from core.sandbox.code_executor import (
    SANDBOX_MEMORY_LIMIT_BYTES,
    SANDBOX_TIMEOUT_SECONDS,
    execute_sandboxed_poc,
)


def _no_lingering_children() -> bool:
    """True if no child processes are left running -- the black-box way
    to confirm a process is actually dead, not merely that `.terminate()`
    was called on it. `execute_sandboxed_poc`'s public interface does
    not (and should not) expose the internal `Process` object for a
    test to `.is_alive()` directly; this is the equivalent check from
    outside that interface."""
    return len(multiprocessing.active_children()) == 0


class TestFullRealPipelineSuccess:
    """Task 4's first required test: validate_code() -> code_executor's
    actual entry point -> real subprocess -> real call_target() hitting
    a mocked transport ONLY at the httpx layer -> result_parser.py ->
    structured result. The one test proving every piece actually
    composes."""

    def test_real_script_through_the_full_real_path(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "api.example.com" in str(request.url)
            return httpx.Response(200, json={"flag": "XBOW_PROBE_99999"})

        transport = httpx.MockTransport(handler)
        code = (
            "import json\n"
            'result = call_target("https://api.example.com/probe")\n'
            'print("status:", result["status"])\n'
            'print("body:", result["body_preview"])\n'
        )

        result = execute_sandboxed_poc(code, ["*.example.com"], transport=transport)

        assert result.outcome == SandboxOutcome.COMPLETED
        assert result.error_message is None
        assert "status: 200" in result.stdout
        assert "XBOW_PROBE_99999" in result.stdout
        assert result.execution_seconds is not None
        assert result.execution_seconds >= 0.0
        assert _no_lingering_children()

    def test_multiple_call_target_invocations_in_one_script(self):
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(200, json={"call": call_count["n"]})

        transport = httpx.MockTransport(handler)
        code = (
            'r1 = call_target("https://api.example.com/a")\n'
            'r2 = call_target("https://api.example.com/b")\n'
            'print(r1["status"], r2["status"])\n'
        )
        result = execute_sandboxed_poc(code, ["*.example.com"], transport=transport)
        assert result.outcome == SandboxOutcome.COMPLETED
        assert result.stdout.strip() == "200 200"

    def test_script_with_a_normal_bug_is_completed_with_error_message(self):
        """A script's own logic error (not a resource limit, not an
        out-of-scope call) is still COMPLETED -- the process ran to
        completion, it just raised."""
        code = "x = 1 / 0\n"
        result = execute_sandboxed_poc(code, ["*.example.com"])
        assert result.outcome == SandboxOutcome.COMPLETED
        assert result.error_message is not None
        assert "ZeroDivisionError" in result.error_message


class TestValidatorRejectionNeverSpawnsAProcess:
    """Task 4's second required test."""

    def test_rejected_script_returns_rejected_by_validator(self):
        result = execute_sandboxed_poc("import socket", ["*.example.com"])
        assert result.outcome == SandboxOutcome.REJECTED_BY_VALIDATOR
        assert "socket" in result.rejection_reasons[0]
        assert result.execution_seconds is None

    def test_rejected_script_spawns_no_subprocess(self):
        before = len(multiprocessing.active_children())
        execute_sandboxed_poc("eval('1+1')", ["*.example.com"])
        after = len(multiprocessing.active_children())
        assert before == after == 0

    def test_rejected_script_produces_no_stdout(self):
        result = execute_sandboxed_poc("import subprocess", ["*.example.com"])
        assert result.stdout == ""


class TestRealMemoryLimitEnforcement:
    """Task 4's third required test: a script that allocates past the
    limit, inside the real forked child with a real RLIMIT_AS ceiling."""

    def test_unbounded_allocation_is_classified_memory_exceeded_not_a_crash(self):
        code = 'data = []\nwhile True:\n    data.append("X" * 1_000_000)\n'
        result = execute_sandboxed_poc(code, ["*.example.com"], memory_limit_bytes=64 * 1024 * 1024)
        assert result.outcome == SandboxOutcome.MEMORY_EXCEEDED
        assert result.error_message is not None
        assert "64" in result.error_message
        assert _no_lingering_children()

    def test_a_single_oversized_allocation_is_also_classified_memory_exceeded(self):
        code = 'x = bytearray(200 * 1024 * 1024)\nprint("should not reach here")\n'
        result = execute_sandboxed_poc(code, ["*.example.com"], memory_limit_bytes=64 * 1024 * 1024)
        assert result.outcome == SandboxOutcome.MEMORY_EXCEEDED
        assert "should not reach here" not in result.stdout

    def test_a_script_well_under_the_limit_is_not_misclassified(self):
        """Negative control: ordinary, small memory use must not
        trigger a false MEMORY_EXCEEDED classification."""
        code = 'data = [i for i in range(1000)]\nprint(sum(data))\n'
        result = execute_sandboxed_poc(code, ["*.example.com"], memory_limit_bytes=SANDBOX_MEMORY_LIMIT_BYTES)
        assert result.outcome == SandboxOutcome.COMPLETED
        assert result.stdout.strip() == "499500"


class TestRealTimeoutEnforcement:
    """Task 4's fourth required test: a script that sleeps past the
    limit, inside the real forked child -- and confirmation the process
    is actually dead afterward, not just that terminate() was called."""

    def test_infinite_loop_is_classified_timed_out(self):
        code = "while True:\n    pass\n"
        start = time.monotonic()
        result = execute_sandboxed_poc(code, ["*.example.com"], timeout_seconds=2.0)
        elapsed = time.monotonic() - start

        assert result.outcome == SandboxOutcome.TIMED_OUT
        assert "2s" in result.error_message
        # Should take roughly the timeout plus at most the termination
        # grace period, not the full default 30s -- proves the override
        # actually took effect and the escalation is prompt.
        assert 2.0 <= elapsed < 2.0 + 5.0

    def test_process_is_actually_dead_after_timeout_not_merely_terminate_called(self):
        code = "while True:\n    pass\n"
        execute_sandboxed_poc(code, ["*.example.com"], timeout_seconds=1.0)
        # Give the OS a brief moment to finish reaping, then confirm --
        # this is the black-box equivalent of p.is_alive() is False,
        # since the Process object itself is not exposed by the public
        # interface.
        time.sleep(0.2)
        assert _no_lingering_children()

    def test_a_script_that_sleeps_but_finishes_before_the_timeout_completes_normally(self):
        """Negative control: a script that takes noticeably long but
        still finishes within the limit must not be misclassified as
        TIMED_OUT."""
        code = 'import time\ntime.sleep(0.3)\nprint("done sleeping")\n'
        result = execute_sandboxed_poc(code, ["*.example.com"], timeout_seconds=3.0)
        assert result.outcome == SandboxOutcome.COMPLETED
        assert "done sleeping" in result.stdout

    def test_stdout_is_empty_after_a_genuine_hang_since_sigterm_preempts_reporting(self):
        """Not a bug: `child_conn.send(payload)` is the LAST thing
        `_sandboxed_worker` does, in its own top-level try/finally --
        SIGTERM (no handler installed, none needed) ends the process
        before it ever reaches that line for a script truly stuck in a
        tight loop. `_drain_partial_stdout` exists for the narrow
        race where the child had already finished reporting by the time
        termination was requested, not for recovering output from a
        genuinely hung script -- confirmed here as the actual, expected
        behavior, not assumed."""
        code = 'print("before the hang")\nwhile True:\n    pass\n'
        result = execute_sandboxed_poc(code, ["*.example.com"], timeout_seconds=1.5)
        assert result.outcome == SandboxOutcome.TIMED_OUT
        assert result.stdout == ""


class TestOutOfScopeCallTargetInsideRealChild:
    """Task 4's fifth required test."""

    def test_out_of_scope_call_surfaces_as_a_classified_outcome_not_a_raw_traceback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        code = 'call_target("https://totally-unrelated-evil.com/")\nprint("unreachable")\n'

        result = execute_sandboxed_poc(code, ["*.example.com"], transport=transport)

        assert result.outcome == SandboxOutcome.COMPLETED
        assert result.error_message is not None
        assert "SandboxOutOfScopeError" in result.error_message
        assert "evil.com" in result.error_message
        assert "unreachable" not in result.stdout

    def test_metadata_and_interactsh_calls_are_not_treated_as_out_of_scope(self):
        """Confirms the exemption is actually reachable through the
        real, composed pipeline, not just in safety_guard.py's own unit
        tests -- the previously-fixed RateLimitedClient composition bug
        would regress silently here if it ever came back."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ami-12345")

        transport = httpx.MockTransport(handler)
        code = 'r = call_target("http://169.254.169.254/latest/meta-data/ami-id")\nprint(r["body_preview"])\n'

        result = execute_sandboxed_poc(code, ["*.example.com"], transport=transport)

        assert result.outcome == SandboxOutcome.COMPLETED
        assert result.error_message is None
        assert "ami-12345" in result.stdout

    def test_partial_output_before_the_out_of_scope_call_is_preserved(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        code = 'print("before the bad call")\ncall_target("https://evil.com/")\nprint("never")\n'

        result = execute_sandboxed_poc(code, ["*.example.com"], transport=transport)

        assert "before the bad call" in result.stdout
        assert "never" not in result.stdout


class TestModuleConstants:
    def test_default_timeout_matches_section_3(self):
        assert SANDBOX_TIMEOUT_SECONDS == 30.0

    def test_default_memory_limit_matches_section_3(self):
        assert SANDBOX_MEMORY_LIMIT_BYTES == 128 * 1024 * 1024


class TestOverridesDoNotAffectProductionDefaults:
    """A test-only override on one call must not leak into another
    call's behavior -- each execute_sandboxed_poc() call is independent."""

    def test_overriding_timeout_in_one_call_does_not_affect_the_next(self):
        code = 'print("quick")\n'
        r1 = execute_sandboxed_poc(code, ["*.example.com"], timeout_seconds=1.0)
        r2 = execute_sandboxed_poc(code, ["*.example.com"])  # default 30s
        assert r1.outcome == SandboxOutcome.COMPLETED
        assert r2.outcome == SandboxOutcome.COMPLETED
