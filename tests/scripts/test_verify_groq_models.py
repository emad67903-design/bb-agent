"""
Implements: Section 3 test coverage -- verify_groq_models.py
Blueprint: bb_agent_v6.6_final_blueprint.md

All Groq network calls are mocked -- this sandbox's network allowlist
does not include api.groq.com. Live verification should be re-run in an
environment with that egress before Week 0 sign-off.
"""

import pytest
import requests
import yaml

from scripts.verify_groq_models import (
    GroqVerificationError,
    _get_api_key,
    fetch_live_model_ids,
    load_configured_models,
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


class TestFetchLiveModelIds:
    def test_returns_sorted_ids(self, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            return_value=_FakeResponse(200, {"data": [{"id": "b-model"}, {"id": "a-model"}]}),
        )
        assert fetch_live_model_ids("fake-key") == ["a-model", "b-model"]

    def test_network_error_raises(self, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            side_effect=requests.ConnectionError("no route"),
        )
        with pytest.raises(GroqVerificationError, match="request failed"):
            fetch_live_model_ids("fake-key")

    def test_non_200_raises(self, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            return_value=_FakeResponse(401, text="unauthorized"),
        )
        with pytest.raises(GroqVerificationError, match="401"):
            fetch_live_model_ids("bad-key")

    def test_malformed_payload_raises(self, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            return_value=_FakeResponse(200, {"unexpected": "shape"}),
        )
        with pytest.raises(GroqVerificationError, match="Unexpected"):
            fetch_live_model_ids("fake-key")


class TestLoadConfiguredModels:
    def test_reads_both_fields(self, llm_config):
        path = llm_config({"groq_strategy_model": "strat-1", "groq_report_model": "report-1"})
        assert load_configured_models(path) == ("strat-1", "report-1")

    def test_empty_strings_when_unset(self, llm_config):
        path = llm_config({"gemini_model": "gemini-2.5-pro"})
        assert load_configured_models(path) == ("", "")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GroqVerificationError, match="not found"):
            load_configured_models(tmp_path / "missing.yaml")

    def test_malformed_yaml_raises(self, tmp_path):
        path = tmp_path / "llm_config.yaml"
        path.write_text("gemini_model: [unclosed", encoding="utf-8")
        with pytest.raises(GroqVerificationError, match="not valid YAML"):
            load_configured_models(path)


class TestGetApiKey:
    def test_raises_when_keyring_empty(self, mocker):
        mocker.patch("scripts.verify_groq_models.keyring.get_password", return_value=None)
        with pytest.raises(GroqVerificationError, match="OS keyring"):
            _get_api_key()

    def test_returns_key_when_present(self, mocker):
        mocker.patch("scripts.verify_groq_models.keyring.get_password", return_value="stored-key")
        assert _get_api_key() == "stored-key"


class TestVerify:
    def test_all_live_true_when_both_configured_models_present(self, llm_config, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            return_value=_FakeResponse(200, {"data": [{"id": "strat-1"}, {"id": "report-1"}]}),
        )
        path = llm_config({"groq_strategy_model": "strat-1", "groq_report_model": "report-1"})
        result = verify(path, api_key="fake-key")
        assert result.all_live is True
        assert result.strategy_model_live is True
        assert result.report_model_live is True

    def test_all_live_false_when_unset(self, llm_config, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            return_value=_FakeResponse(200, {"data": [{"id": "strat-1"}]}),
        )
        path = llm_config({})
        result = verify(path, api_key="fake-key")
        assert result.all_live is False
        assert result.strategy_model_live is False
        assert result.report_model_live is False

    def test_all_live_false_when_configured_model_not_in_live_list(self, llm_config, mocker):
        mocker.patch(
            "scripts.verify_groq_models.requests.get",
            return_value=_FakeResponse(200, {"data": [{"id": "some-other-model"}]}),
        )
        path = llm_config({"groq_strategy_model": "deprecated-model", "groq_report_model": "report-1"})
        result = verify(path, api_key="fake-key")
        assert result.strategy_model_live is False
        assert result.report_model_live is False
