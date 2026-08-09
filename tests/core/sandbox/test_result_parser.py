"""
Implements: Section 10.7 test coverage -- core/sandbox/result_parser.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from core.ontology.sandbox import SandboxOutcome
from core.sandbox.result_parser import (
    STDOUT_PREVIEW_CHARS,
    build_completed_result,
    build_memory_exceeded_result,
    build_rejected_result,
    build_timed_out_result,
)
from core.sandbox.sandbox_validator import ValidationResult


class TestBuildRejectedResult:
    def test_basic_shape(self):
        r = build_rejected_result(ValidationResult(is_valid=False, violations=["blocked import: socket"]))
        assert r.outcome == SandboxOutcome.REJECTED_BY_VALIDATOR
        assert r.stdout == ""
        assert r.execution_seconds is None
        assert r.rejection_reasons == ["blocked import: socket"]

    def test_multiple_violations_preserved_in_order(self):
        violations = ["blocked import: socket", "blocked import: pickle", "blocked name referenced: eval"]
        r = build_rejected_result(ValidationResult(is_valid=False, violations=violations))
        assert r.rejection_reasons == violations
        assert r.error_message == "; ".join(violations)


class TestBuildCompletedResult:
    def test_clean_run(self):
        r = build_completed_result(stdout="hello\n", exception_text=None, execution_seconds=0.5)
        assert r.outcome == SandboxOutcome.COMPLETED
        assert r.stdout == "hello\n"
        assert r.error_message is None
        assert r.rejection_reasons == []
        assert r.execution_seconds == 0.5

    def test_script_raised_exception(self):
        r = build_completed_result(
            stdout="partial output\n", exception_text="ValueError: bad thing", execution_seconds=0.2
        )
        assert r.outcome == SandboxOutcome.COMPLETED
        assert r.error_message == "ValueError: bad thing"
        assert r.stdout == "partial output\n"

    def test_empty_stdout(self):
        r = build_completed_result(stdout="", exception_text=None, execution_seconds=0.01)
        assert r.stdout == ""


class TestBuildTimedOutResult:
    def test_basic_shape(self):
        r = build_timed_out_result(stdout="still going\n", execution_seconds=30.1, timeout_seconds=30.0)
        assert r.outcome == SandboxOutcome.TIMED_OUT
        assert r.stdout == "still going\n"
        assert "30s" in r.error_message
        assert r.execution_seconds == 30.1
        assert r.rejection_reasons == []

    def test_message_reflects_the_actual_configured_limit_not_a_hardcoded_30(self):
        r = build_timed_out_result(stdout="", execution_seconds=5.0, timeout_seconds=5.0)
        assert "5s" in r.error_message
        assert "30s" not in r.error_message

    def test_empty_stdout_when_nothing_was_printed_before_timeout(self):
        r = build_timed_out_result(stdout="", execution_seconds=30.0, timeout_seconds=30.0)
        assert r.stdout == ""


class TestBuildMemoryExceededResult:
    def test_basic_shape(self):
        r = build_memory_exceeded_result(stdout="", execution_seconds=1.2, memory_limit_bytes=128 * 1024 * 1024)
        assert r.outcome == SandboxOutcome.MEMORY_EXCEEDED
        assert "128" in r.error_message
        assert "MB" in r.error_message
        assert r.rejection_reasons == []

    def test_message_reflects_a_different_configured_limit(self):
        r = build_memory_exceeded_result(stdout="", execution_seconds=1.0, memory_limit_bytes=64 * 1024 * 1024)
        assert "64" in r.error_message
        assert "128" not in r.error_message

    def test_partial_stdout_preserved(self):
        r = build_memory_exceeded_result(
            stdout="got this far\n", execution_seconds=0.5, memory_limit_bytes=128 * 1024 * 1024
        )
        assert r.stdout == "got this far\n"


class TestStdoutTruncation:
    def test_short_stdout_unchanged(self):
        r = build_completed_result(stdout="short", exception_text=None, execution_seconds=0.1)
        assert r.stdout == "short"

    def test_exactly_at_the_cap_unchanged(self):
        stdout = "X" * STDOUT_PREVIEW_CHARS
        r = build_completed_result(stdout=stdout, exception_text=None, execution_seconds=0.1)
        assert r.stdout == stdout
        assert not r.stdout.endswith("[truncated]")

    def test_over_the_cap_truncated_with_marker(self):
        stdout = "X" * (STDOUT_PREVIEW_CHARS + 1000)
        r = build_completed_result(stdout=stdout, exception_text=None, execution_seconds=0.1)
        assert len(r.stdout) == STDOUT_PREVIEW_CHARS + len("\n...[truncated]")
        assert r.stdout.endswith("[truncated]")
        assert r.stdout.startswith("X" * 100)

    def test_truncation_applies_to_timed_out_results_too(self):
        stdout = "Y" * (STDOUT_PREVIEW_CHARS + 500)
        r = build_timed_out_result(stdout=stdout, execution_seconds=30.0, timeout_seconds=30.0)
        assert r.stdout.endswith("[truncated]")

    def test_truncation_applies_to_memory_exceeded_results_too(self):
        stdout = "Z" * (STDOUT_PREVIEW_CHARS + 500)
        r = build_memory_exceeded_result(stdout=stdout, execution_seconds=1.0, memory_limit_bytes=128 * 1024 * 1024)
        assert r.stdout.endswith("[truncated]")

    def test_rejected_result_stdout_is_never_truncated_it_is_always_empty(self):
        """REJECTED_BY_VALIDATOR never ran any code, so there is nothing
        to truncate -- build_rejected_result intentionally does not call
        the truncation helper at all."""
        r = build_rejected_result(ValidationResult(is_valid=False, violations=["x"]))
        assert r.stdout == ""
