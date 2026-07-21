"""
Implements: Section 3 -- cli/main.py ("PreflightChecker (checks listed
by name, not count) + argparse")
Blueprint: bb_agent_v6.6_final_blueprint.md

All 26 named preflight checks (Section 3's cli/main.py comment block),
registered as one flat, declarative tuple -- PREFLIGHT_CHECKS -- per the
Engineering Constitution's file-organization guidance: this is "the same
kind of thing many times, structurally identical" (like the 29-entry
SCANNER_REGISTRY), so it is one long registry, not fragmented across
files.

WEEK 0 SCOPE: two checks are NOT_YET_IMPLEMENTED (agreed resolution,
item 3), because their target code does not exist yet:
  - webhook_binding:  needs core/triggers/webhook_trigger.py (Week 2)
  - scanner_ram_gate: needs SCANNER_REGISTRY (Week 5) + the 29 scanner
                       implementations (Week 7)
Both report NOT_YET_IMPLEMENTED rather than crashing on import or
silently PASSing -- see CheckStatus. Both go live automatically once
their target code exists; no registry change is needed then.

Every check is `async def` for interface uniformity (one check
interface, matching "one scanner interface" -- base_scanner.py -- and
"one HTTP layer" -- elsewhere in this codebase); blocking I/O
(subprocess, socket bind, sync client calls) is wrapped in
asyncio.to_thread so a slow check cannot block the others if the runner
is ever changed to run checks concurrently.

MANY of these checks depend on infrastructure this project's Week 0
BUILD sandbox does not provision (Redis, PostgreSQL, Ollama, nuclei,
Playwright's downloaded Chromium, live Groq/Gemini API keys, egress to
interactsh.com). Run against those, they correctly FAIL CLOSED with a
connection-error detail -- that is correct preflight behavior, not a
bug in the check. See the Week 0 completion report for exactly which
checks were exercised live in this sandbox vs. logic-verified only via
mocked unit tests.
"""

from __future__ import annotations

import argparse
import asyncio
import filecmp
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

from core.governance.scope_config_generator import ScopeConfigError, generate_scope_allowed_json
from scripts import payload_inventory
from scripts.interactsh_setup import run_setup as run_interactsh_setup
from scripts.measure_baseline_ram import BaselineRamReport, measure_scanner_registry
from scripts.verify_gemini_models import GeminiVerificationError
from scripts.verify_gemini_models import verify as verify_gemini
from scripts.verify_groq_models import GroqVerificationError
from scripts.verify_groq_models import verify as verify_groq

REPO_ROOT = Path(__file__).resolve().parent.parent


class CheckStatus(str, Enum):
    """Outcome of one preflight check."""

    PASS = "pass"
    FAIL = "fail"
    NOT_YET_IMPLEMENTED = "not_yet_implemented"


@dataclass(frozen=True)
class PreflightCheckResult:
    """Result of running one PreflightCheck.

    Attributes:
        name: Matches the check's registry name (Section 3).
        status: PASS, FAIL, or NOT_YET_IMPLEMENTED.
        detail: Human-readable explanation.
    """

    name: str
    status: CheckStatus
    detail: str


@dataclass
class PreflightContext:
    """Paths and settings every check needs. Centralized so tests can
    point every check at a tmp_path sandbox instead of the real repo.

    Attributes:
        repo_root: Project root.
        llm_config_path: configs/llm_config.yaml.
        scope_yaml_path: configs/scope.yaml.
        scope_allowed_json_path: services/scope_allowed.json (output).
        race_engine_dir: services/race_engine/.
        smuggling_engine_dir: services/smuggling_engine/.
        payloads_dir: data/payloads/.
        payload_engine_path: core/knowledge/payload_engine.py (Week 7+).
        webhook_trigger_path: core/triggers/webhook_trigger.py (Week 2+).
        redis_url: redis:// URL for the redis_connection / asyncio_redis checks.
        postgres_dsn: postgresql:// DSN for the postgres_connection check.
        keyring_service: OS keyring service name (Section 3: keyring_secrets).
        nuclei_templates_dir: nuclei-templates checkout directory.
        scanner_registry: SCANNER_REGISTRY (Week 5+); None until it exists.
    """

    repo_root: Path = REPO_ROOT
    llm_config_path: Path = field(default_factory=lambda: REPO_ROOT / "configs" / "llm_config.yaml")
    scope_yaml_path: Path = field(default_factory=lambda: REPO_ROOT / "configs" / "scope.yaml")
    scope_allowed_json_path: Path = field(default_factory=lambda: REPO_ROOT / "services" / "scope_allowed.json")
    race_engine_dir: Path = field(default_factory=lambda: REPO_ROOT / "services" / "race_engine")
    smuggling_engine_dir: Path = field(default_factory=lambda: REPO_ROOT / "services" / "smuggling_engine")
    payloads_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "payloads")
    payload_engine_path: Path = field(default_factory=lambda: REPO_ROOT / "core" / "knowledge" / "payload_engine.py")
    webhook_trigger_path: Path = field(
        default_factory=lambda: REPO_ROOT / "core" / "triggers" / "webhook_trigger.py"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    postgres_dsn: str = "postgresql://127.0.0.1:5432/bbagent"
    keyring_service: str = "bb-agent"
    nuclei_templates_dir: Path = field(default_factory=lambda: Path.home() / "nuclei-templates")
    scanner_registry: dict[str, object] | None = None


@dataclass(frozen=True)
class PreflightCheck:
    """One registry entry: a name (Section 3) plus its async check function."""

    name: str
    run: Callable[[PreflightContext], Awaitable[PreflightCheckResult]]


# --------------------------------------------------------------------
# Individual checks. Each returns a PreflightCheckResult and never raises
# -- a check that can't run cleanly reports FAIL/NOT_YET_IMPLEMENTED with
# a detail string; it does not propagate an exception to the runner.
# --------------------------------------------------------------------


async def check_python_version(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: python_version >= 3.11."""
    ok = sys.version_info >= (3, 11)
    detail = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return PreflightCheckResult("python_version", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _check_import(module_name: str) -> tuple[bool, str]:
    import importlib

    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:
        return False, str(exc)
    return True, getattr(mod, "__version__", "import succeeded")


async def check_zstandard(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: zstandard import succeeds."""
    ok, detail = await asyncio.to_thread(_check_import, "zstandard")
    return PreflightCheckResult("zstandard", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def check_scipy(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: scipy import succeeds."""
    ok, detail = await asyncio.to_thread(_check_import, "scipy")
    return PreflightCheckResult("scipy", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _check_playwright_chromium() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, f"playwright not installed: {exc}"
    try:
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
            if exe.is_file():
                return True, str(exe)
            return False, f"chromium executable not found at {exe}"
    except Exception as exc:  # noqa: BLE001 -- boundary to a third-party
        # library whose failure modes are not enumerable in advance;
        # classified into a clean FAIL detail rather than crashing preflight.
        return False, str(exc)


async def check_playwright_browsers(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: playwright_browsers -- chromium installed."""
    ok, detail = await asyncio.to_thread(_check_playwright_chromium)
    return PreflightCheckResult("playwright_browsers", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _check_redis_ping(redis_url: str) -> tuple[bool, str]:
    try:
        import redis as redis_sync
    except ImportError as exc:
        return False, f"redis package not installed: {exc}"
    try:
        client = redis_sync.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        pong = client.ping()
        return bool(pong), "PING succeeded" if pong else "PING returned falsy"
    except Exception as exc:  # noqa: BLE001 -- any connection failure is a
        # clean FAIL for this check, not a preflight crash.
        return False, str(exc)


async def check_redis_connection(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: redis_connection -- PING succeeds."""
    ok, detail = await asyncio.to_thread(_check_redis_ping, ctx.redis_url)
    return PreflightCheckResult("redis_connection", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _check_postgres_open(dsn: str) -> tuple[bool, str]:
    try:
        import psycopg
    except ImportError as exc:
        return False, f"psycopg package not installed: {exc}"
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            del conn
        return True, "connection opened"
    except Exception as exc:  # noqa: BLE001 -- any connection failure is a
        # clean FAIL for this check, not a preflight crash.
        return False, str(exc)


async def check_postgres_connection(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: postgres_connection -- connection opens."""
    ok, detail = await asyncio.to_thread(_check_postgres_open, ctx.postgres_dsn)
    return PreflightCheckResult("postgres_connection", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _check_keyring_secrets(service: str) -> tuple[bool, str]:
    import keyring

    required = ("GROQ_API_KEY", "GEMINI_API_KEY", "TELEGRAM_TOKEN")
    try:
        missing = [name for name in required if not keyring.get_password(service, name)]
    except Exception as exc:  # noqa: BLE001 -- a keyring BACKEND failure
        # (e.g. keyring.errors.NoKeyringError when no OS keyring backend
        # is installed at all, as in this build sandbox) is a distinct
        # failure mode from "the secret isn't stored" but must be a clean
        # FAIL either way, not an exception escaping to the runner.
        return False, f"keyring backend error: {exc}"
    if missing:
        return False, f"missing from OS keyring (service={service!r}): {', '.join(missing)}"
    return True, f"all {len(required)} secrets present in OS keyring"


async def check_keyring_secrets(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: keyring_secrets -- GROQ_API_KEY, GEMINI_API_KEY,
    TELEGRAM_TOKEN in OS keyring."""
    ok, detail = await asyncio.to_thread(_check_keyring_secrets, ctx.keyring_service)
    return PreflightCheckResult("keyring_secrets", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def check_groq_strategy_model(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: groq_strategy_model live (verify_groq_models.py)."""
    try:
        result = await asyncio.to_thread(verify_groq, ctx.llm_config_path)
    except GroqVerificationError as exc:
        return PreflightCheckResult("groq_strategy_model", CheckStatus.FAIL, str(exc))
    status = CheckStatus.PASS if result.strategy_model_live else CheckStatus.FAIL
    detail = f"{result.groq_strategy_model!r} live={result.strategy_model_live}"
    return PreflightCheckResult("groq_strategy_model", status, detail)


async def check_groq_report_model(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: groq_report_model live (verify_groq_models.py)."""
    try:
        result = await asyncio.to_thread(verify_groq, ctx.llm_config_path)
    except GroqVerificationError as exc:
        return PreflightCheckResult("groq_report_model", CheckStatus.FAIL, str(exc))
    status = CheckStatus.PASS if result.report_model_live else CheckStatus.FAIL
    detail = f"{result.groq_report_model!r} live={result.report_model_live}"
    return PreflightCheckResult("groq_report_model", status, detail)


async def check_gemini_model(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: gemini_model -- gemini-2.5-pro live (verify_gemini_models.py)."""
    try:
        result = await asyncio.to_thread(verify_gemini, ctx.llm_config_path)
    except GeminiVerificationError as exc:
        return PreflightCheckResult("gemini_model", CheckStatus.FAIL, str(exc))
    status = CheckStatus.PASS if result.configured_model_live else CheckStatus.FAIL
    detail = f"{result.configured_model!r} live={result.configured_model_live}"
    return PreflightCheckResult("gemini_model", status, detail)


def _run_subprocess(args: list[str], cwd: Path | None = None, timeout: int = 30) -> tuple[bool, str]:
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return False, f"binary not found: {exc}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    ok = proc.returncode == 0
    output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return ok, output[:500]


async def check_go_version(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: go_version >= 1.21."""
    ok, output = await asyncio.to_thread(_run_subprocess, ["go", "version"])
    if not ok:
        return PreflightCheckResult("go_version", CheckStatus.FAIL, output)
    # "go version go1.22.2 linux/amd64" -> ("1", "22", "2")
    try:
        version_token = output.split()[2]  # "go1.22.2"
        major, minor, *_ = version_token.removeprefix("go").split(".")
        meets_min = (int(major), int(minor)) >= (1, 21)
    except (IndexError, ValueError):
        return PreflightCheckResult("go_version", CheckStatus.FAIL, f"unparsable output: {output}")
    status = CheckStatus.PASS if meets_min else CheckStatus.FAIL
    return PreflightCheckResult("go_version", status, output)


async def check_go_build_race(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: services/race_engine/ builds successfully."""
    ok, output = await asyncio.to_thread(_run_subprocess, ["go", "build", "./..."], ctx.race_engine_dir)
    return PreflightCheckResult("go_build_race", CheckStatus.PASS if ok else CheckStatus.FAIL, output or "build OK")


async def check_go_build_smuggle(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: services/smuggling_engine/ builds successfully."""
    ok, output = await asyncio.to_thread(_run_subprocess, ["go", "build", "./..."], ctx.smuggling_engine_dir)
    return PreflightCheckResult("go_build_smuggle", CheckStatus.PASS if ok else CheckStatus.FAIL, output or "build OK")


def _scope_guard_paths_identical(race_dir: Path, smuggle_dir: Path) -> tuple[bool, str]:
    race_path = race_dir / "scope_guard.go"
    smuggle_path = smuggle_dir / "scope_guard.go"
    if not race_path.is_file() or not smuggle_path.is_file():
        return False, f"missing file(s): race={race_path.is_file()} smuggle={smuggle_path.is_file()}"
    identical = filecmp.cmp(race_path, smuggle_path, shallow=False)
    return identical, "byte-identical" if identical else "DRIFTED -- Section 8.5 requires byte-identical"


async def check_go_scope_diff(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: go_scope_diff -- race/scope_guard.go == smuggling/scope_guard.go
    (byte-identical). Re-implements `make ci-scope-diff` directly in
    Python so the CLI preflight has no dependency on `make` being
    installed at agent-startup time."""
    ok, detail = await asyncio.to_thread(_scope_guard_paths_identical, ctx.race_engine_dir, ctx.smuggling_engine_dir)
    return PreflightCheckResult("go_scope_diff", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _port_is_free(port: int) -> tuple[bool, str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True, f"port {port} is free"
    except OSError as exc:
        return False, f"port {port} is BUSY: {exc}"
    finally:
        s.close()


async def check_port_18080(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: port_18080 -- port free (lsof returns empty).
    Implemented as a direct bind-test rather than shelling out to lsof
    (which may not be installed everywhere): binding and immediately
    releasing a socket is a more portable and more direct proof of "free"
    than parsing lsof's text output."""
    ok, detail = await asyncio.to_thread(_port_is_free, 18080)
    return PreflightCheckResult("port_18080", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def check_port_18081(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: port_18081 -- port free."""
    ok, detail = await asyncio.to_thread(_port_is_free, 18081)
    return PreflightCheckResult("port_18081", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def check_scope_json_gen(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: scope_json_gen -- generates services/scope_allowed.json
    from scope.yaml."""
    try:
        result = await asyncio.to_thread(
            generate_scope_allowed_json, ctx.scope_yaml_path, ctx.scope_allowed_json_path
        )
    except ScopeConfigError as exc:
        return PreflightCheckResult("scope_json_gen", CheckStatus.FAIL, str(exc))
    detail = f"wrote {result.pattern_count} pattern(s) to {result.output_path}"
    return PreflightCheckResult("scope_json_gen", CheckStatus.PASS, detail)


async def check_interactsh_conn(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: interactsh_conn -- public interactsh responds OR
    self-hosted starts."""
    result = await asyncio.to_thread(run_interactsh_setup)
    status = CheckStatus.PASS if result.preflight_ok else CheckStatus.FAIL
    detail = f"mode={result.mode.value} detail={result.detail}"
    return PreflightCheckResult("interactsh_conn", status, detail)


async def check_payload_inventory(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: payload_inventory -- payload_inventory.py passes."""
    try:
        report = await asyncio.to_thread(payload_inventory.run_inventory, ctx.payloads_dir, ctx.payload_engine_path)
    except payload_inventory.PayloadInventoryError as exc:
        return PreflightCheckResult("payload_inventory", CheckStatus.FAIL, str(exc))
    status = CheckStatus.PASS if report.ok else CheckStatus.FAIL
    detail = (
        f"missing={len(report.missing_files)} mismatches={len(report.type_mismatches)} "
        f"payload_engine_check={report.payload_engine_check}"
    )
    return PreflightCheckResult("payload_inventory", status, detail)


async def check_nuclei_binary(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: nuclei_binary -- nuclei -version succeeds."""
    ok, output = await asyncio.to_thread(_run_subprocess, ["nuclei", "-version"])
    return PreflightCheckResult("nuclei_binary", CheckStatus.PASS if ok else CheckStatus.FAIL, output)


def _nuclei_templates_age_days(templates_dir: Path) -> tuple[bool, str]:
    if not templates_dir.is_dir():
        return False, f"{templates_dir} does not exist"
    age_seconds = time.time() - templates_dir.stat().st_mtime
    age_days = age_seconds / 86400
    ok = age_days < 30
    return ok, f"{templates_dir} mtime age = {age_days:.1f} days"


async def check_nuclei_templates(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: nuclei_templates -- nuclei-templates mtime < 30 days."""
    ok, detail = await asyncio.to_thread(_nuclei_templates_age_days, ctx.nuclei_templates_dir)
    return PreflightCheckResult("nuclei_templates", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def check_scanner_ram_gate(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: scanner_ram_gate -- measure_baseline_ram.py: no scanner
    > 200 MB.

    DEFERRED (agreed resolution, item 3): SCANNER_REGISTRY does not exist
    until Week 5 and the 29 scanners do not exist until Week 7 -- there is
    nothing to measure yet. Goes live automatically once
    ctx.scanner_registry is populated.
    """
    if not ctx.scanner_registry:
        return PreflightCheckResult(
            "scanner_ram_gate",
            CheckStatus.NOT_YET_IMPLEMENTED,
            "SCANNER_REGISTRY does not exist until Week 5; scanners until Week 7 (Section 12)",
        )
    measurements = await asyncio.to_thread(measure_scanner_registry, ctx.scanner_registry)
    report = BaselineRamReport(scanner_measurements=measurements)
    status = CheckStatus.FAIL if report.preflight_failure else CheckStatus.PASS
    detail = f"top-5-at-concurrency-5={report.total_at_concurrency_5_mb:.1f} MB"
    return PreflightCheckResult("scanner_ram_gate", status, detail)


def _webhook_binds_localhost_only(webhook_trigger_path: Path) -> tuple[bool, str]:
    text = webhook_trigger_path.read_text(encoding="utf-8")
    if '"127.0.0.1"' not in text and "'127.0.0.1'" not in text:
        return False, "no 127.0.0.1 literal found in webhook_trigger.py"
    if '"0.0.0.0"' in text or "'0.0.0.0'" in text:
        return False, "0.0.0.0 literal found in webhook_trigger.py -- Section 8.5 forbids this"
    return True, "127.0.0.1 binding present; no 0.0.0.0 literal"


async def check_webhook_binding(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: webhook_binding -- webhook_trigger.py socket == 127.0.0.1.

    DEFERRED (agreed resolution, item 3): core/triggers/webhook_trigger.py
    does not exist until Week 2. Goes live automatically once that file
    exists, matching Section 3's own annotation: "127.0.0.1 binding
    ENFORCED IN CODE -- verified in preflight".
    """
    if not ctx.webhook_trigger_path.is_file():
        return PreflightCheckResult(
            "webhook_binding",
            CheckStatus.NOT_YET_IMPLEMENTED,
            "core/triggers/webhook_trigger.py does not exist until Week 2 (Section 12)",
        )
    ok, detail = await asyncio.to_thread(_webhook_binds_localhost_only, ctx.webhook_trigger_path)
    return PreflightCheckResult("webhook_binding", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def check_ollama_model(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: ollama_model -- ollama list shows qwen2.5-coder:7b."""
    ok, output = await asyncio.to_thread(_run_subprocess, ["ollama", "list"])
    if not ok:
        return PreflightCheckResult("ollama_model", CheckStatus.FAIL, output)
    found = "qwen2.5-coder:7b" in output
    detail = "qwen2.5-coder:7b present" if found else f"qwen2.5-coder:7b NOT in `ollama list` output: {output}"
    return PreflightCheckResult("ollama_model", CheckStatus.PASS if found else CheckStatus.FAIL, detail)


def _check_chromadb_heartbeat() -> tuple[bool, str]:
    try:
        import chromadb
    except ImportError as exc:
        return False, f"chromadb package not installed: {exc}"
    try:
        client = chromadb.EphemeralClient()
        heartbeat_ns = client.heartbeat()
        return True, f"heartbeat() = {heartbeat_ns}"
    except Exception as exc:  # noqa: BLE001 -- any client-init/heartbeat
        # failure is a clean FAIL for this check, not a preflight crash.
        return False, str(exc)


async def check_chromadb_conn(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: chromadb_conn -- ChromaDB client.heartbeat() succeeds."""
    ok, detail = await asyncio.to_thread(_check_chromadb_heartbeat)
    return PreflightCheckResult("chromadb_conn", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


async def _async_redis_ping_via_to_thread(redis_url: str) -> tuple[bool, str]:
    """Verifies the actual pattern named by the check: a synchronous
    redis-py client's blocking call, wrapped in asyncio.to_thread so it
    is safe to call from this project's asyncio-based runtime (Section
    6's workflow is async throughout; Redis per Section 8.2 is a sync
    client that must not block the event loop)."""
    try:
        import redis as redis_sync
    except ImportError as exc:
        return False, f"redis package not installed: {exc}"
    try:
        client = redis_sync.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        pong = await asyncio.to_thread(client.ping)
        return bool(pong), "asyncio.to_thread(redis_client.ping) succeeded"
    except Exception as exc:  # noqa: BLE001 -- any connection failure is a
        # clean FAIL for this check, not a preflight crash.
        return False, str(exc)


async def check_asyncio_redis(ctx: PreflightContext) -> PreflightCheckResult:
    """Section 3: asyncio_redis -- asyncio.to_thread wrapper for sync
    Redis verified. This exercises the actual to_thread(sync_call)
    pattern end to end (not merely a redis_connection re-check) --
    distinct from check_redis_connection, which proves the server is
    reachable at all."""
    ok, detail = await _async_redis_ping_via_to_thread(ctx.redis_url)
    return PreflightCheckResult("asyncio_redis", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


# --------------------------------------------------------------------
# Registry -- one flat, declarative tuple of all 26 named checks
# (Section 3). Order matches Section 3's own listing.
# --------------------------------------------------------------------

PREFLIGHT_CHECKS: tuple[PreflightCheck, ...] = (
    PreflightCheck("python_version", check_python_version),
    PreflightCheck("zstandard", check_zstandard),
    PreflightCheck("scipy", check_scipy),
    PreflightCheck("playwright_browsers", check_playwright_browsers),
    PreflightCheck("redis_connection", check_redis_connection),
    PreflightCheck("postgres_connection", check_postgres_connection),
    PreflightCheck("keyring_secrets", check_keyring_secrets),
    PreflightCheck("groq_strategy_model", check_groq_strategy_model),
    PreflightCheck("groq_report_model", check_groq_report_model),
    PreflightCheck("gemini_model", check_gemini_model),
    PreflightCheck("go_version", check_go_version),
    PreflightCheck("go_build_race", check_go_build_race),
    PreflightCheck("go_build_smuggle", check_go_build_smuggle),
    PreflightCheck("go_scope_diff", check_go_scope_diff),
    PreflightCheck("port_18080", check_port_18080),
    PreflightCheck("port_18081", check_port_18081),
    PreflightCheck("scope_json_gen", check_scope_json_gen),
    PreflightCheck("interactsh_conn", check_interactsh_conn),
    PreflightCheck("payload_inventory", check_payload_inventory),
    PreflightCheck("nuclei_binary", check_nuclei_binary),
    PreflightCheck("nuclei_templates", check_nuclei_templates),
    PreflightCheck("scanner_ram_gate", check_scanner_ram_gate),
    PreflightCheck("webhook_binding", check_webhook_binding),
    PreflightCheck("ollama_model", check_ollama_model),
    PreflightCheck("chromadb_conn", check_chromadb_conn),
    PreflightCheck("asyncio_redis", check_asyncio_redis),
)

assert len(PREFLIGHT_CHECKS) == 26, f"expected 26 preflight checks, found {len(PREFLIGHT_CHECKS)}"


async def run_all_checks(ctx: PreflightContext | None = None) -> list[PreflightCheckResult]:
    """Runs every registered check in registry order and returns all results.

    Checks run sequentially (not gathered concurrently): several of them
    (go_build_race, go_build_smuggle) share filesystem/build-cache state,
    and preflight is a startup-time gate, not a hot path -- correctness
    and readable ordering matter more here than wall-clock time.

    Args:
        ctx: Overrides the default PreflightContext (used by tests).

    Returns:
        One PreflightCheckResult per registered check, in registry order.
    """
    context = ctx or PreflightContext()
    results = []
    for check in PREFLIGHT_CHECKS:
        result = await check.run(context)
        results.append(result)
    return results


def _print_report(results: list[PreflightCheckResult]) -> int:
    """Prints a report and returns the process exit code (Section 3:
    "any failure blocks startup"). NOT_YET_IMPLEMENTED does not block
    startup on its own -- it is a Week 0-6 scaffolding state, not a
    failure -- but a genuine FAIL always does."""
    symbol = {CheckStatus.PASS: "PASS", CheckStatus.FAIL: "FAIL", CheckStatus.NOT_YET_IMPLEMENTED: "TODO"}
    name_width = max(len(r.name) for r in results)
    for r in results:
        print(f"[{symbol[r.status]:>4}] {r.name.ljust(name_width)}  {r.detail}")

    failed = [r for r in results if r.status == CheckStatus.FAIL]
    not_impl = [r for r in results if r.status == CheckStatus.NOT_YET_IMPLEMENTED]
    passed = [r for r in results if r.status == CheckStatus.PASS]
    print(f"\n{len(passed)} passed, {len(failed)} failed, {len(not_impl)} not yet implemented "
          f"(of {len(results)} total)")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BB-Agent preflight checks (Section 3)")
    parser.parse_args()
    results = asyncio.run(run_all_checks())
    return _print_report(results)


if __name__ == "__main__":
    raise SystemExit(main())
