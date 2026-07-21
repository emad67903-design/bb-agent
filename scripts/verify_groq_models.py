#!/usr/bin/env python3
"""
Implements: Section 3 -- scripts/verify_groq_models.py ("Queries
/openai/v1/models live; validates BOTH tier IDs")
Blueprint: bb_agent_v6.6_final_blueprint.md

Preflight dependency for the "groq_strategy_model" and "groq_report_model"
checks (Section 3, cli/main.py comment block). Section 9.3 / R-M6:
llama-3.1-8b-instant and llama-3.3-70b-versatile deprecate 2026-08-16 --
this script is the ONLY sanctioned way to confirm a configured model ID
is currently live; model IDs are never hardcoded (Section 13: "Hardcoded
Groq model IDs" is a permanently rejected item).

Secrets are read from the OS keyring (Section 3 preflight check
"keyring_secrets"), never from plain environment variables or files.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyring
import requests
import yaml

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
KEYRING_SERVICE = "bb-agent"
KEYRING_GROQ_KEY_NAME = "GROQ_API_KEY"
REQUEST_TIMEOUT_SECONDS = 15


class GroqVerificationError(Exception):
    """Raised when live Groq model verification cannot be completed."""


@dataclass(frozen=True)
class GroqVerificationResult:
    """Outcome of verifying configured Groq model IDs against the live list.

    Attributes:
        live_model_ids: All model IDs Groq currently reports as available.
        groq_strategy_model: The configured strategy-tier model ID.
        groq_report_model: The configured report-tier model ID.
        strategy_model_live: Whether groq_strategy_model is in live_model_ids.
        report_model_live: Whether groq_report_model is in live_model_ids.
    """

    live_model_ids: list[str]
    groq_strategy_model: str
    groq_report_model: str
    strategy_model_live: bool
    report_model_live: bool

    @property
    def all_live(self) -> bool:
        return self.strategy_model_live and self.report_model_live


def _get_api_key() -> str:
    """Fetches GROQ_API_KEY from the OS keyring (Section 3: keyring_secrets).

    Raises:
        GroqVerificationError: If no key is stored, OR the keyring
            backend itself fails (e.g. keyring.errors.NoKeyringError
            when no OS keyring backend is installed) -- both must
            surface as THIS script's exception type, not an arbitrary
            one, so every caller's `except GroqVerificationError` catches
            both failure modes uniformly.
    """
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_GROQ_KEY_NAME)
    except Exception as exc:  # noqa: BLE001 -- keyring backend failures
        # are not enumerable in advance across platforms; re-raised as
        # this module's own exception type rather than left as a raw,
        # uncaught third-party exception.
        raise GroqVerificationError(f"OS keyring backend error: {exc}") from exc
    if not key:
        raise GroqVerificationError(
            f"No {KEYRING_GROQ_KEY_NAME} found in OS keyring under service "
            f"'{KEYRING_SERVICE}'. Store it with: "
            f"keyring.set_password('{KEYRING_SERVICE}', '{KEYRING_GROQ_KEY_NAME}', '<your key>')"
        )
    return key


def fetch_live_model_ids(api_key: str) -> list[str]:
    """Queries Groq's OpenAI-compatible /models endpoint.

    Args:
        api_key: A valid Groq API key.

    Returns:
        Sorted list of all live model IDs.

    Raises:
        GroqVerificationError: On network failure or a non-200 response.
    """
    try:
        resp = requests.get(
            GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GroqVerificationError(f"Groq /models request failed: {exc}") from exc

    if resp.status_code != 200:
        raise GroqVerificationError(f"Groq /models returned HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        payload: Any = resp.json()
        ids = sorted(item["id"] for item in payload["data"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GroqVerificationError(f"Unexpected Groq /models response shape: {exc}") from exc

    return ids


def load_configured_models(llm_config_path: Path) -> tuple[str, str]:
    """Reads groq_strategy_model and groq_report_model from llm_config.yaml.

    Args:
        llm_config_path: Path to configs/llm_config.yaml.

    Returns:
        (groq_strategy_model, groq_report_model) as configured. An empty
        string means "MUST SET; no default" (Section 3) has not been set.

    Raises:
        GroqVerificationError: If the file is missing or malformed.
    """
    if not llm_config_path.is_file():
        raise GroqVerificationError(f"llm_config.yaml not found at {llm_config_path}")
    try:
        raw: Any = yaml.safe_load(llm_config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GroqVerificationError(f"llm_config.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise GroqVerificationError("llm_config.yaml did not parse to a mapping")

    return raw.get("groq_strategy_model", "") or "", raw.get("groq_report_model", "") or ""


def verify(llm_config_path: Path, api_key: str | None = None) -> GroqVerificationResult:
    """Runs the full verification: fetch live models, compare to configured.

    Args:
        llm_config_path: Path to configs/llm_config.yaml.
        api_key: Overrides the keyring lookup (used by tests).

    Returns:
        A GroqVerificationResult. Callers decide pass/fail via `.all_live`
        -- an empty configured model ID is correctly reported as not live.
    """
    key = api_key if api_key is not None else _get_api_key()
    live_ids = fetch_live_model_ids(key)
    strategy_model, report_model = load_configured_models(llm_config_path)

    return GroqVerificationResult(
        live_model_ids=live_ids,
        groq_strategy_model=strategy_model,
        groq_report_model=report_model,
        strategy_model_live=bool(strategy_model) and strategy_model in live_ids,
        report_model_live=bool(report_model) and report_model in live_ids,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-config", type=Path, default=Path("configs/llm_config.yaml"))
    args = parser.parse_args()

    try:
        result = verify(args.llm_config)
    except GroqVerificationError as exc:
        print(f"[GROQ_VERIFY_FAILED] {exc}", file=sys.stderr)
        return 1

    print(f"[GROQ_VERIFY] {len(result.live_model_ids)} live model(s) found")
    for label, model_id, is_live in (
        ("groq_strategy_model", result.groq_strategy_model, result.strategy_model_live),
        ("groq_report_model", result.groq_report_model, result.report_model_live),
    ):
        if not model_id:
            print(f"[GROQ_VERIFY_FAILED] {label} is not set in llm_config.yaml (Section 3: no default)")
        elif is_live:
            print(f"[GROQ_VERIFY_OK] {label}={model_id!r} is live")
        else:
            print(f"[GROQ_VERIFY_FAILED] {label}={model_id!r} is NOT in Groq's live model list")

    if not result.all_live:
        print("\nLive candidates (choose current IDs from this list -- Section 9.3 / R-M6):")
        for mid in result.live_model_ids:
            print(f"  - {mid}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
