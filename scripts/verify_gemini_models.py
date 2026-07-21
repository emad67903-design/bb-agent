#!/usr/bin/env python3
"""
Implements: Section 3 -- scripts/verify_gemini_models.py ("Queries
/v1beta/models live; validates gemini-2.5-pro")
Blueprint: bb_agent_v6.6_final_blueprint.md

Preflight dependency for the "gemini_model" check (Section 3, cli/main.py
comment block). Section 9.3 (C1): gemini-2.5-pro-preview-06-05 was
deprecated 2025-12-02; only the stable gemini-2.5-pro channel is used,
and this script is what blocks startup if it is ever not live.
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

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
KEYRING_SERVICE = "bb-agent"
KEYRING_GEMINI_KEY_NAME = "GEMINI_API_KEY"
REQUEST_TIMEOUT_SECONDS = 15
EXPECTED_MODEL = "gemini-2.5-pro"  # Section 9.3 / Section 13: stable channel only


class GeminiVerificationError(Exception):
    """Raised when live Gemini model verification cannot be completed."""


@dataclass(frozen=True)
class GeminiVerificationResult:
    """Outcome of verifying the configured Gemini model against the live list.

    Attributes:
        live_model_names: All model names Gemini currently reports.
        configured_model: The gemini_model value read from llm_config.yaml.
        configured_model_live: Whether configured_model is in live_model_names.
    """

    live_model_names: list[str]
    configured_model: str
    configured_model_live: bool


def _get_api_key() -> str:
    """Fetches GEMINI_API_KEY from the OS keyring (Section 3: keyring_secrets).

    Raises:
        GeminiVerificationError: If no key is stored, OR the keyring
            backend itself fails (e.g. keyring.errors.NoKeyringError
            when no OS keyring backend is installed) -- both must
            surface as THIS script's exception type (mirrors the
            verify_groq_models.py fix for the identical failure mode).
    """
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_GEMINI_KEY_NAME)
    except Exception as exc:  # noqa: BLE001 -- keyring backend failures
        # are not enumerable in advance across platforms; re-raised as
        # this module's own exception type rather than left as a raw,
        # uncaught third-party exception.
        raise GeminiVerificationError(f"OS keyring backend error: {exc}") from exc
    if not key:
        raise GeminiVerificationError(
            f"No {KEYRING_GEMINI_KEY_NAME} found in OS keyring under service "
            f"'{KEYRING_SERVICE}'. Store it with: "
            f"keyring.set_password('{KEYRING_SERVICE}', '{KEYRING_GEMINI_KEY_NAME}', '<your key>')"
        )
    return key


def fetch_live_model_names(api_key: str) -> list[str]:
    """Queries Gemini's /v1beta/models endpoint.

    Args:
        api_key: A valid Gemini API key.

    Returns:
        Sorted list of live model names, with any "models/" prefix
        stripped so they compare directly against llm_config.yaml's
        gemini_model value (e.g. "gemini-2.5-pro").

    Raises:
        GeminiVerificationError: On network failure or a non-200 response.
    """
    try:
        resp = requests.get(
            GEMINI_MODELS_URL,
            params={"key": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GeminiVerificationError(f"Gemini /models request failed: {exc}") from exc

    if resp.status_code != 200:
        raise GeminiVerificationError(f"Gemini /models returned HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        payload: Any = resp.json()
        names = sorted(item["name"].removeprefix("models/") for item in payload["models"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeminiVerificationError(f"Unexpected Gemini /models response shape: {exc}") from exc

    return names


def load_configured_model(llm_config_path: Path) -> str:
    """Reads gemini_model from llm_config.yaml.

    Args:
        llm_config_path: Path to configs/llm_config.yaml.

    Returns:
        The configured value, defaulting to EXPECTED_MODEL if the key is
        absent (Section 3: "gemini_model: str = 'gemini-2.5-pro'").

    Raises:
        GeminiVerificationError: If the file is missing or malformed.
    """
    if not llm_config_path.is_file():
        raise GeminiVerificationError(f"llm_config.yaml not found at {llm_config_path}")
    try:
        raw: Any = yaml.safe_load(llm_config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GeminiVerificationError(f"llm_config.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise GeminiVerificationError("llm_config.yaml did not parse to a mapping")

    return raw.get("gemini_model") or EXPECTED_MODEL


def verify(llm_config_path: Path, api_key: str | None = None) -> GeminiVerificationResult:
    """Runs the full verification: fetch live models, compare to configured.

    Args:
        llm_config_path: Path to configs/llm_config.yaml.
        api_key: Overrides the keyring lookup (used by tests).
    """
    key = api_key if api_key is not None else _get_api_key()
    live_names = fetch_live_model_names(key)
    configured = load_configured_model(llm_config_path)

    return GeminiVerificationResult(
        live_model_names=live_names,
        configured_model=configured,
        configured_model_live=configured in live_names,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-config", type=Path, default=Path("configs/llm_config.yaml"))
    args = parser.parse_args()

    try:
        result = verify(args.llm_config)
    except GeminiVerificationError as exc:
        print(f"[GEMINI_VERIFY_FAILED] {exc}", file=sys.stderr)
        return 1

    print(f"[GEMINI_VERIFY] {len(result.live_model_names)} live model(s) found")
    if result.configured_model != EXPECTED_MODEL:
        print(
            f"[GEMINI_VERIFY_WARN] configured gemini_model={result.configured_model!r} "
            f"differs from the blueprint's stable channel {EXPECTED_MODEL!r} (Section 13)"
        )
    if result.configured_model_live:
        print(f"[GEMINI_VERIFY_OK] {result.configured_model!r} is live")
        return 0

    print(f"[GEMINI_VERIFY_FAILED] {result.configured_model!r} is NOT in Gemini's live model list")
    print("\nLive candidates:")
    for name in result.live_model_names:
        print(f"  - {name}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
