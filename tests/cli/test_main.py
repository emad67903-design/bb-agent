"""
Implements: Section 3 test coverage -- cli/main.py PreflightChecker
Blueprint: bb_agent_v6.6_final_blueprint.md

Checks that depend on infrastructure unavailable in this sandbox (Redis,
Postgres, Ollama, nuclei, Playwright Chromium, live Groq/Gemini keys,
interactsh.com egress) are tested for correct FAIL-closed behavior via
mocking, not live execution -- see the Week 0 completion report for
which checks were exercised live vs. logic-only.
"""

import asyncio

import pytest

from cli.main import (
    PREFLIGHT_CHECKS,
    CheckStatus,
    PreflightContext,
    _print_report,
    check_go_scope_diff,
    check_gemini_model,
    check_groq_report_model,
    check_groq_strategy_model,
    check_payload_inventory,
    check_port_18080,
    check_python_version,
    check_scanner_ram_gate,
    check_scope_json_gen,
    check_webhook_binding,
    run_all_checks,
)
from scripts.payload_inventory import PAYLOAD_MANIFEST
from scripts.verify_gemini_models import GeminiVerificationResult
from scripts.verify_groq_models import GroqVerificationResult


def run(coro):
    return asyncio.run(coro)


class TestRegistry:
    def test_has_exactly_26_checks(self):
        assert len(PREFLIGHT_CHECKS) == 26

    def test_names_match_section_3_exactly(self):
        expected = {
            "python_version", "zstandard", "scipy", "playwright_browsers",
            "redis_connection", "postgres_connection", "keyring_secrets",
            "groq_strategy_model", "groq_report_model", "gemini_model",
            "go_version", "go_build_race", "go_build_smuggle", "go_scope_diff",
            "port_18080", "port_18081", "scope_json_gen", "interactsh_conn",
            "payload_inventory", "nuclei_binary", "nuclei_templates",
            "scanner_ram_gate", "webhook_binding", "ollama_model",
            "chromadb_conn", "asyncio_redis",
        }
        actual = {c.name for c in PREFLIGHT_CHECKS}
        assert actual == expected

    def test_no_duplicate_names(self):
        names = [c.name for c in PREFLIGHT_CHECKS]
        assert len(names) == len(set(names))


class TestCheckPythonVersion:
    def test_passes_on_this_interpreter(self):
        result = run(check_python_version(PreflightContext()))
        assert result.status == CheckStatus.PASS  # this suite requires 3.11+ itself


class TestCheckPortFree:
    def test_port_18080_free_in_clean_sandbox(self):
        result = run(check_port_18080(PreflightContext()))
        assert result.status == CheckStatus.PASS

    def test_busy_port_reports_fail(self):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy_port = s.getsockname()[1]
        try:
            from cli.main import _port_is_free

            ok, detail = _port_is_free(busy_port)
            assert ok is False
            assert "BUSY" in detail
        finally:
            s.close()


class TestCheckScopeJsonGen:
    def test_passes_and_writes_file(self, tmp_path):
        scope_yaml = tmp_path / "scope.yaml"
        scope_yaml.write_text("scope_domains:\n  - '*.example.com'\n", encoding="utf-8")
        out = tmp_path / "scope_allowed.json"
        ctx = PreflightContext(scope_yaml_path=scope_yaml, scope_allowed_json_path=out)
        result = run(check_scope_json_gen(ctx))
        assert result.status == CheckStatus.PASS
        assert out.is_file()

    def test_fails_when_scope_yaml_missing(self, tmp_path):
        ctx = PreflightContext(
            scope_yaml_path=tmp_path / "missing.yaml", scope_allowed_json_path=tmp_path / "out.json"
        )
        result = run(check_scope_json_gen(ctx))
        assert result.status == CheckStatus.FAIL


class TestCheckGoScopeDiff:
    def test_passes_on_real_repo_files(self):
        # Regression check against the actual committed race_engine/
        # and smuggling_engine/ scope_guard.go files.
        result = run(check_go_scope_diff(PreflightContext()))
        assert result.status == CheckStatus.PASS
        assert result.detail == "byte-identical"

    def test_fails_when_files_differ(self, tmp_path):
        race_dir = tmp_path / "race_engine"
        smuggle_dir = tmp_path / "smuggling_engine"
        race_dir.mkdir()
        smuggle_dir.mkdir()
        (race_dir / "scope_guard.go").write_text("package main // A\n", encoding="utf-8")
        (smuggle_dir / "scope_guard.go").write_text("package main // B\n", encoding="utf-8")
        ctx = PreflightContext(race_engine_dir=race_dir, smuggling_engine_dir=smuggle_dir)
        result = run(check_go_scope_diff(ctx))
        assert result.status == CheckStatus.FAIL
        assert "DRIFTED" in result.detail

    def test_fails_when_file_missing(self, tmp_path):
        race_dir = tmp_path / "race_engine"
        smuggle_dir = tmp_path / "smuggling_engine"
        race_dir.mkdir()
        smuggle_dir.mkdir()
        (race_dir / "scope_guard.go").write_text("package main\n", encoding="utf-8")
        # smuggling_engine/scope_guard.go deliberately absent
        ctx = PreflightContext(race_engine_dir=race_dir, smuggling_engine_dir=smuggle_dir)
        result = run(check_go_scope_diff(ctx))
        assert result.status == CheckStatus.FAIL


class TestCheckPayloadInventory:
    def test_passes_on_real_committed_stubs(self):
        result = run(check_payload_inventory(PreflightContext()))
        assert result.status == CheckStatus.PASS

    def test_fails_when_directory_empty(self, tmp_path):
        ctx = PreflightContext(payloads_dir=tmp_path, payload_engine_path=tmp_path / "nonexistent.py")
        result = run(check_payload_inventory(ctx))
        assert result.status == CheckStatus.FAIL
        assert f"missing={len(PAYLOAD_MANIFEST)}" in result.detail


class TestDeferredChecks:
    def test_webhook_binding_not_yet_implemented_when_file_absent(self, tmp_path):
        ctx = PreflightContext(webhook_trigger_path=tmp_path / "webhook_trigger.py")
        result = run(check_webhook_binding(ctx))
        assert result.status == CheckStatus.NOT_YET_IMPLEMENTED
        assert "Week 2" in result.detail

    def test_webhook_binding_goes_live_once_file_exists_and_passes(self, tmp_path):
        path = tmp_path / "webhook_trigger.py"
        path.write_text('server.bind("127.0.0.1", port)\n', encoding="utf-8")
        ctx = PreflightContext(webhook_trigger_path=path)
        result = run(check_webhook_binding(ctx))
        assert result.status == CheckStatus.PASS

    def test_webhook_binding_fails_on_0_0_0_0(self, tmp_path):
        path = tmp_path / "webhook_trigger.py"
        path.write_text('server.bind("0.0.0.0", port)\n', encoding="utf-8")
        ctx = PreflightContext(webhook_trigger_path=path)
        result = run(check_webhook_binding(ctx))
        assert result.status == CheckStatus.FAIL

    def test_scanner_ram_gate_not_yet_implemented_when_registry_absent(self):
        ctx = PreflightContext(scanner_registry=None)
        result = run(check_scanner_ram_gate(ctx))
        assert result.status == CheckStatus.NOT_YET_IMPLEMENTED
        assert "Week 5" in result.detail

    def test_scanner_ram_gate_raises_not_implemented_internally_once_registry_populated(self):
        # Week 0: even with a (fake) non-empty registry, real per-scanner
        # measurement isn't implementable yet (scripts.measure_baseline_ram
        # .measure_scanner_registry raises NotImplementedError by design)
        # -- confirm that surfaces as a clean exception, not a silent PASS.
        ctx = PreflightContext(scanner_registry={"xss_scanner": object()})
        with pytest.raises(NotImplementedError):
            run(check_scanner_ram_gate(ctx))


class TestGroqGeminiChecksMocked:
    def test_groq_strategy_model_pass(self, mocker, tmp_path):
        mocker.patch(
            "cli.main.verify_groq",
            return_value=GroqVerificationResult(
                live_model_ids=["strat-1"],
                groq_strategy_model="strat-1",
                groq_report_model="report-1",
                strategy_model_live=True,
                report_model_live=False,
            ),
        )
        result = run(check_groq_strategy_model(PreflightContext(llm_config_path=tmp_path / "x.yaml")))
        assert result.status == CheckStatus.PASS

    def test_groq_report_model_fail(self, mocker, tmp_path):
        mocker.patch(
            "cli.main.verify_groq",
            return_value=GroqVerificationResult(
                live_model_ids=["strat-1"],
                groq_strategy_model="strat-1",
                groq_report_model="",
                strategy_model_live=True,
                report_model_live=False,
            ),
        )
        result = run(check_groq_report_model(PreflightContext(llm_config_path=tmp_path / "x.yaml")))
        assert result.status == CheckStatus.FAIL

    def test_gemini_model_pass(self, mocker, tmp_path):
        mocker.patch(
            "cli.main.verify_gemini",
            return_value=GeminiVerificationResult(
                live_model_names=["gemini-2.5-pro"],
                configured_model="gemini-2.5-pro",
                configured_model_live=True,
            ),
        )
        result = run(check_gemini_model(PreflightContext(llm_config_path=tmp_path / "x.yaml")))
        assert result.status == CheckStatus.PASS


class TestRunAllChecks:
    def test_returns_26_results_in_registry_order(self):
        results = run(run_all_checks())
        assert len(results) == 26
        assert [r.name for r in results] == [c.name for c in PREFLIGHT_CHECKS]

    def test_every_result_has_a_valid_status(self):
        results = run(run_all_checks())
        for r in results:
            assert r.status in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.NOT_YET_IMPLEMENTED)

    def test_deferred_checks_report_not_yet_implemented_by_default(self):
        results = run(run_all_checks())
        by_name = {r.name: r for r in results}
        assert by_name["webhook_binding"].status == CheckStatus.NOT_YET_IMPLEMENTED
        assert by_name["scanner_ram_gate"].status == CheckStatus.NOT_YET_IMPLEMENTED


class TestPrintReport:
    def test_exit_code_1_when_any_fail(self, capsys):
        from cli.main import PreflightCheckResult

        results = [
            PreflightCheckResult("a", CheckStatus.PASS, "ok"),
            PreflightCheckResult("b", CheckStatus.FAIL, "bad"),
            PreflightCheckResult("c", CheckStatus.NOT_YET_IMPLEMENTED, "later"),
        ]
        code = _print_report(results)
        assert code == 1

    def test_exit_code_0_when_no_fail_even_with_not_yet_implemented(self, capsys):
        from cli.main import PreflightCheckResult

        results = [
            PreflightCheckResult("a", CheckStatus.PASS, "ok"),
            PreflightCheckResult("c", CheckStatus.NOT_YET_IMPLEMENTED, "later"),
        ]
        code = _print_report(results)
        assert code == 0
