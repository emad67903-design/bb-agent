"""
Implements: shared infrastructure for Section 3's `boundary_identifier.py`
(Groq call 1), `assumption_extractor.py` (Groq call 2), and
`exploitability_scorer.py` (Groq calls 3+4) -- per
mental_model_builder_prompt_design.md Section 7: "reuse [`verify_groq_
models.py`'s] pattern for the actual request-sending code... rather than
inventing a second one."
Blueprint: bb_agent_v6.6_final_blueprint.md

PRIVATE HELPER (leading underscore), per the Engineering Constitution:
"A private helper module (leading underscore, e.g. cli/_preflight_checks.py,
imported only by the Section-3-named file it supports) is allowed
without a stop-condition flag, since it introduces no new public path or
interface." This module introduces no new public path Section 3 names
-- it exists only so `boundary_identifier.py`, `assumption_extractor.py`,
and `exploitability_scorer.py` don't each duplicate the same keyring
lookup + HTTP-retry + JSON-parsing logic three times.

Reuses `scripts/verify_groq_models.py`'s exact conventions rather than
inventing new ones: same `KEYRING_SERVICE`/key name, same
keyring-backend-error-wrapping pattern, same "read
`configs/llm_config.yaml` directly via `yaml.safe_load`" approach (no
`config.py`/`AgentConfig` exists yet in this repository -- confirmed by
directory listing; mental_model_builder_prompt_design.md Section 7
explicitly says not to design one here).

Does NOT reuse `verify_groq_models.py`'s `/models` endpoint -- this
module calls Groq's `/chat/completions` endpoint (also OpenAI-compatible,
same host, same auth scheme) to actually run a call, not to list models.
`groq_strategy_model`'s value is still expected to have been verified
live by `verify_groq_models.py` at preflight time (Section 3, cli/main.py's
`groq_strategy_model` preflight check) -- this module does not
re-verify liveness on every call, matching the precedent that liveness
checking is preflight's job, not every call site's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyring
import requests
import yaml

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
KEYRING_SERVICE = "bb-agent"
KEYRING_GROQ_KEY_NAME = "GROQ_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60  # Groq calls synthesize/reason over larger
                              # input than /models' liveness check;
                              # verify_groq_models.py's 15s budget is not
                              # reused for this reason (documented
                              # judgment call, not a citation).
MAX_RETRIES = 2  # 1 initial attempt + up to 2 retries = 3 total, matching
                 # "ordinary transient-error retry, no special logging"
                 # (mental_model_builder_prompt_design.md Section 6).


class GroqCallError(Exception):
    """Raised when a Groq chat-completion call cannot be completed --
    keyring failure, network failure, non-200 response, or a response
    that isn't valid JSON after all retries."""


@dataclass(frozen=True)
class GroqCallResult:
    """A successfully parsed Groq call result.

    Attributes:
        parsed: The JSON-decoded response body content (the model's
            reply), already `json.loads`-ed by this module -- callers
            receive a dict/list, not a raw string to parse themselves.
        raw_content: The exact string the model returned, kept
            alongside `parsed` for logging/debugging without needing to
            re-serialize.
    """

    parsed: Any
    raw_content: str


def _get_api_key() -> str:
    """Fetches GROQ_API_KEY from the OS keyring.

    Identical convention to `scripts/verify_groq_models.py`'s
    `_get_api_key` -- same service/key name, same error-wrapping -- so
    a single keyring entry serves both preflight verification and
    actual calls.

    Raises:
        GroqCallError: If no key is stored, or the keyring backend
            itself fails.
    """
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_GROQ_KEY_NAME)
    except Exception as exc:  # noqa: BLE001 -- keyring backend failures
        # are not enumerable in advance across platforms (same
        # rationale as verify_groq_models.py's identical catch).
        raise GroqCallError(f"OS keyring backend error: {exc}") from exc
    if not key:
        raise GroqCallError(
            f"No {KEYRING_GROQ_KEY_NAME} found in OS keyring under service "
            f"'{KEYRING_SERVICE}'."
        )
    return key


def load_groq_strategy_model(llm_config_path: Path) -> str:
    """Reads `groq_strategy_model` from `llm_config.yaml`.

    Same file, same key, same "empty string means unset" convention as
    `scripts/verify_groq_models.py`'s `load_configured_models` -- not a
    second reader of the same file with different behavior.

    Args:
        llm_config_path: Path to `configs/llm_config.yaml`.

    Returns:
        The configured model ID, or `""` if unset.

    Raises:
        GroqCallError: If the file is missing or malformed.
    """
    if not llm_config_path.is_file():
        raise GroqCallError(f"llm_config.yaml not found at {llm_config_path}")
    try:
        raw: Any = yaml.safe_load(llm_config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GroqCallError(f"llm_config.yaml is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise GroqCallError("llm_config.yaml did not parse to a mapping")
    return raw.get("groq_strategy_model", "") or ""


def call_groq_json(
    system_prompt: str,
    user_prompt: str,
    model: str,
    *,
    api_key: str | None = None,
    max_retries: int = MAX_RETRIES,
) -> GroqCallResult:
    """Makes one Groq chat-completion call and parses the response as JSON.

    Retries on transient failures (network error, non-200, or a
    non-JSON response body) up to `max_retries` times before raising --
    "ordinary transient-error retry, no special logging"
    (mental_model_builder_prompt_design.md Section 6); this is NOT
    `GROQ_LIMIT_HIT` (Section 8.3), which is specifically for budget
    exhaustion, not transient failures. Callers map budget-exhaustion
    handling (routing to local-7B, `[DEGRADED_MODE]`) themselves, since
    that decision depends on session-level call counts this module has
    no visibility into.

    Args:
        system_prompt: The system message content.
        user_prompt: The user message content.
        model: The live-verified Groq model ID
            (`groq_strategy_model`, verified live by
            `scripts/verify_groq_models.py` at preflight).
        api_key: Overrides the keyring lookup (used by tests).
        max_retries: Maximum retry attempts after the first failure.

    Returns:
        A `GroqCallResult` with the parsed JSON body.

    Raises:
        GroqCallError: If every attempt fails -- keyring error (raised
            immediately, not retried, since retrying a missing key
            can't succeed), or network/non-200/malformed-JSON
            persisting across all retries.
    """
    key = api_key if api_key is not None else _get_api_key()

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                GROQ_CHAT_COMPLETIONS_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_error = GroqCallError(f"Groq chat-completions request failed: {exc}")
            continue

        if resp.status_code != 200:
            last_error = GroqCallError(
                f"Groq chat-completions returned HTTP {resp.status_code}: {resp.text[:500]}"
            )
            continue

        try:
            payload: Any = resp.json()
            content: str = payload["choices"][0]["message"]["content"]
            parsed: Any = _json_loads_strict(content)
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            last_error = GroqCallError(f"Unexpected/malformed Groq response: {exc}")
            continue

        return GroqCallResult(parsed=parsed, raw_content=content)

    assert last_error is not None  # loop always sets this before falling through
    raise last_error


def _json_loads_strict(content: str) -> Any:
    """`json.loads`, raising the same way regardless of Python version
    quirks -- isolated to one place so callers all fail the same way on
    malformed model output."""
    import json

    return json.loads(content)
