"""
Implements: test coverage for core/mental_model/_groq_client.py
Blueprint: bb_agent_v6.6_final_blueprint.md

All HTTP and keyring access is mocked -- no real network calls, no real
credentials, matching this project's own preflight-vs-runtime
separation (verify_groq_models.py already covers real-key liveness
checking; this suite covers this module's own logic).
"""

import json

import pytest
import yaml

from core.mental_model._groq_client import (
    GroqCallError,
    call_groq_json,
    load_groq_strategy_model,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


def _groq_success_response(content_obj: dict) -> _FakeResponse:
    return _FakeResponse(
        200,
        json_body={"choices": [{"message": {"content": json.dumps(content_obj)}}]},
    )


class TestCallGroqJson:
    def test_successful_call_returns_parsed_json(self, mocker):
        mocker.patch(
            "core.mental_model._groq_client.requests.post",
            return_value=_groq_success_response({"foo": "bar"}),
        )
        result = call_groq_json("sys", "user", "some-model", api_key="k")
        assert result.parsed == {"foo": "bar"}
        assert result.raw_content == '{"foo": "bar"}'

    def test_sends_expected_request_shape(self, mocker):
        mock_post = mocker.patch(
            "core.mental_model._groq_client.requests.post",
            return_value=_groq_success_response({"x": 1}),
        )
        call_groq_json("system text", "user text", "model-id", api_key="secret-key")
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert kwargs["json"]["model"] == "model-id"
        assert kwargs["json"]["messages"] == [
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "user text"},
        ]
        assert kwargs["json"]["response_format"] == {"type": "json_object"}

    def test_non_200_retries_then_raises(self, mocker):
        mock_post = mocker.patch(
            "core.mental_model._groq_client.requests.post",
            return_value=_FakeResponse(500, text="server error"),
        )
        with pytest.raises(GroqCallError, match="HTTP 500"):
            call_groq_json("sys", "user", "model", api_key="k", max_retries=2)
        assert mock_post.call_count == 3  # 1 initial + 2 retries

    def test_malformed_json_content_retries_then_raises(self, mocker):
        mocker.patch(
            "core.mental_model._groq_client.requests.post",
            return_value=_FakeResponse(
                200, json_body={"choices": [{"message": {"content": "not valid json"}}]}
            ),
        )
        with pytest.raises(GroqCallError, match="malformed Groq response"):
            call_groq_json("sys", "user", "model", api_key="k", max_retries=1)

    def test_missing_choices_key_retries_then_raises(self, mocker):
        mocker.patch(
            "core.mental_model._groq_client.requests.post",
            return_value=_FakeResponse(200, json_body={"unexpected": "shape"}),
        )
        with pytest.raises(GroqCallError, match="malformed Groq response"):
            call_groq_json("sys", "user", "model", api_key="k", max_retries=0)

    def test_succeeds_after_one_transient_failure(self, mocker):
        """Retry must actually recover on a later success, not just
        exhaust and raise -- pins the happy retry path, not only the
        all-attempts-fail path."""
        mock_post = mocker.patch(
            "core.mental_model._groq_client.requests.post",
            side_effect=[
                _FakeResponse(500, text="transient"),
                _groq_success_response({"recovered": True}),
            ],
        )
        result = call_groq_json("sys", "user", "model", api_key="k", max_retries=2)
        assert result.parsed == {"recovered": True}
        assert mock_post.call_count == 2

    def test_network_exception_is_wrapped_and_retried(self, mocker):
        import requests

        mocker.patch(
            "core.mental_model._groq_client.requests.post",
            side_effect=requests.ConnectionError("dns failure"),
        )
        with pytest.raises(GroqCallError, match="request failed"):
            call_groq_json("sys", "user", "model", api_key="k", max_retries=1)

    def test_missing_api_key_raises_without_any_http_call(self, mocker):
        mock_post = mocker.patch("core.mental_model._groq_client.requests.post")
        mocker.patch("core.mental_model._groq_client.keyring.get_password", return_value=None)
        with pytest.raises(GroqCallError, match="No GROQ_API_KEY"):
            call_groq_json("sys", "user", "model")
        mock_post.assert_not_called()

    def test_keyring_backend_error_is_wrapped(self, mocker):
        mocker.patch(
            "core.mental_model._groq_client.keyring.get_password",
            side_effect=RuntimeError("no backend available"),
        )
        with pytest.raises(GroqCallError, match="OS keyring backend error"):
            call_groq_json("sys", "user", "model")


class TestLoadGroqStrategyModel:
    def test_reads_configured_model(self, tmp_path):
        path = tmp_path / "llm_config.yaml"
        path.write_text(yaml.safe_dump({"groq_strategy_model": "llama-x"}), encoding="utf-8")
        assert load_groq_strategy_model(path) == "llama-x"

    def test_missing_key_returns_empty_string(self, tmp_path):
        path = tmp_path / "llm_config.yaml"
        path.write_text(yaml.safe_dump({"other_key": "value"}), encoding="utf-8")
        assert load_groq_strategy_model(path) == ""

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GroqCallError, match="not found"):
            load_groq_strategy_model(tmp_path / "does_not_exist.yaml")

    def test_reads_the_real_repo_llm_config(self):
        """Sanity check against the actual configs/llm_config.yaml, not
        just synthetic fixtures -- same pattern used for
        scope_enforcer.py's end-to-end test."""
        from pathlib import Path

        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "llm_config.yaml"
        # Only asserts this doesn't raise and returns a string -- the
        # actual configured value is deployment-specific and may
        # legitimately be "" (Section 3: "MUST SET; no default").
        assert isinstance(load_groq_strategy_model(real_path), str)
