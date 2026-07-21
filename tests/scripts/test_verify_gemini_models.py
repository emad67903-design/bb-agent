"""
Implements: Section 3 test coverage -- verify_gemini_models.py
Blueprint: bb_agent_v6.6_final_blueprint.md

All Gemini network calls are mocked -- this sandbox's network allowlist
does not include generativelanguage.googleapis.com. Live verification
should be re-run in an environment with that egress before Week 0
sign-off.
"""

import pytest
import requests
import yaml

from scripts.verify_gemini_models import (
    EXPECTED_MODEL,
    GeminiVerificationError,
    _get_api_key,
    fetch_live_model_names,
    load_configured_model,
    verify,
)


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data


@pytest.fixture
def llm_config(tmp_path):
    def _write(content: dict):
        path = tmp_path / "llm_config.yaml"
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        return path

    return _write


class TestFetchLiveModelNames:
    def test_strips_models_prefix_and_sorts(self, mocker):
        mocker.patch(
            "scripts.verify_gemini_models.requests.get",
            return_value=_FakeResponse(
                200, {"models": [{"name": "models/gemini-2.5-flash"}, {"name": "models/gemini-2.5-pro"}]}
            ),
        )
        assert fetch_live_model_names("fake-key") == ["gemini-2.5-flash", "gemini-2.5-pro"]

    def test_network_error_raises(self, mocker):
        mocker.patch(
            "scripts.verify_gemini_models.requests.get",
            side_effect=requests.ConnectionError("no route"),
        )
        with pytest.raises(GeminiVerificationError, match="request failed"):
            fetch_live_model_names("fake-key")

    def test_non_200_raises(self, mocker):
        mocker.patch(
            "scripts.verify_gemini_models.requests.get",
            return_value=_FakeResponse(403, text="forbidden"),
        )
        with pytest.raises(GeminiVerificationError, match="403"):
            fetch_live_model_names("bad-key")

    def test_malformed_payload_raises(self, mocker):
        mocker.patch(
            "scripts.verify_gemini_models.requests.get",
            return_value=_FakeResponse(200, {"unexpected": "shape"}),
        )
        with pytest.raises(GeminiVerificationError, match="Unexpected"):
            fetch_live_model_names("fake-key")


class TestLoadConfiguredModel:
    def test_reads_configured_value(self, llm_config):
        path = llm_config({"gemini_model": "gemini-2.5-flash"})
        assert load_configured_model(path) == "gemini-2.5-flash"

    def test_defaults_to_expected_model_when_unset(self, llm_config):
        path = llm_config({"groq_strategy_model": "x"})
        assert load_configured_model(path) == EXPECTED_MODEL

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GeminiVerificationError, match="not found"):
            load_configured_model(tmp_path / "missing.yaml")


class TestGetApiKey:
    def test_raises_when_keyring_empty(self, mocker):
        mocker.patch("scripts.verify_gemini_models.keyring.get_password", return_value=None)
        with pytest.raises(GeminiVerificationError, match="OS keyring"):
            _get_api_key()

    def test_backend_failure_raises_gemini_verification_error_not_raw_exception(self, mocker):
        # Regression test: mirrors the identical fix in verify_groq_models.py.
        import keyring.errors

        mocker.patch(
            "scripts.verify_gemini_models.keyring.get_password",
            side_effect=keyring.errors.NoKeyringError("No recommended backend was available"),
        )
        with pytest.raises(GeminiVerificationError, match="OS keyring backend error"):
            _get_api_key()


class TestVerify:
    def test_live_true_for_stable_model(self, llm_config, mocker):
        mocker.patch(
            "scripts.verify_gemini_models.requests.get",
            return_value=_FakeResponse(200, {"models": [{"name": "models/gemini-2.5-pro"}]}),
        )
        path = llm_config({"gemini_model": "gemini-2.5-pro"})
        result = verify(path, api_key="fake-key")
        assert result.configured_model_live is True

    def test_live_false_for_deprecated_preview_model(self, llm_config, mocker):
        # Section 13: gemini-2.5-pro-preview-06-05 was deprecated 2025-12-02
        # and must not appear in the live list.
        mocker.patch(
            "scripts.verify_gemini_models.requests.get",
            return_value=_FakeResponse(200, {"models": [{"name": "models/gemini-2.5-pro"}]}),
        )
        path = llm_config({"gemini_model": "gemini-2.5-pro-preview-06-05"})
        result = verify(path, api_key="fake-key")
        assert result.configured_model_live is False
