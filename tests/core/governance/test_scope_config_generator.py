"""
Implements: Section 3 test coverage -- scope_allowed.json generator
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import json

import pytest
import yaml

from core.governance.scope_config_generator import (
    ScopeConfigError,
    generate_scope_allowed_json,
    load_scope_domains,
)


@pytest.fixture
def scope_yaml(tmp_path):
    def _write(content: dict):
        path = tmp_path / "scope.yaml"
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        return path

    return _write


class TestLoadScopeDomains:
    def test_loads_wildcard_and_exact_patterns(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["*.example.com", "example.com"]})
        assert load_scope_domains(path) == ["*.example.com", "example.com"]

    def test_deduplicates_preserving_order(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com", "b.com", "a.com"]})
        assert load_scope_domains(path) == ["a.com", "b.com"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ScopeConfigError, match="not found"):
            load_scope_domains(tmp_path / "does_not_exist.yaml")

    def test_missing_scope_domains_key_raises(self, scope_yaml):
        path = scope_yaml({"program_type": "bug_bounty"})
        with pytest.raises(ScopeConfigError, match="scope_domains"):
            load_scope_domains(path)

    def test_empty_scope_domains_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": []})
        with pytest.raises(ScopeConfigError, match="non-empty"):
            load_scope_domains(path)

    def test_non_string_entry_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["good.com", 123]})
        with pytest.raises(ScopeConfigError):
            load_scope_domains(path)

    def test_malformed_yaml_raises(self, tmp_path):
        path = tmp_path / "scope.yaml"
        path.write_text("scope_domains: [unclosed", encoding="utf-8")
        with pytest.raises(ScopeConfigError, match="not valid YAML"):
            load_scope_domains(path)

    def test_not_a_mapping_raises(self, tmp_path):
        path = tmp_path / "scope.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ScopeConfigError, match="scope_domains"):
            load_scope_domains(path)


class TestGenerateScopeAllowedJson:
    def test_writes_expected_shape(self, scope_yaml, tmp_path):
        src = scope_yaml({"scope_domains": ["*.example.com"]})
        out = tmp_path / "services" / "scope_allowed.json"

        result = generate_scope_allowed_json(src, out)

        assert result.pattern_count == 1
        assert out.is_file()
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["allowed_patterns"] == ["*.example.com"]
        assert "generated_at" in doc
        assert doc["source"] == str(src)

    def test_creates_parent_directory(self, scope_yaml, tmp_path):
        src = scope_yaml({"scope_domains": ["a.com"]})
        out = tmp_path / "nested" / "dir" / "scope_allowed.json"
        generate_scope_allowed_json(src, out)
        assert out.is_file()

    def test_go_struct_compatible_field_name(self, scope_yaml, tmp_path):
        # Section 10.8: Go's LoadScopeGuard unmarshals into a struct
        # tagged json:"allowed_patterns" -- this is a cross-language
        # contract test, not just a Python-side shape check. Unknown
        # extra fields (generated_at, source) must not break Go's decode
        # -- verified directly against the Go source in the Week 0
        # completion report's integration check.
        src = scope_yaml({"scope_domains": ["a.com", "*.b.com"]})
        out = tmp_path / "scope_allowed.json"
        generate_scope_allowed_json(src, out)
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert "allowed_patterns" in doc
        assert isinstance(doc["allowed_patterns"], list)
        assert all(isinstance(p, str) for p in doc["allowed_patterns"])

    def test_regeneration_is_stable(self, scope_yaml, tmp_path):
        src = scope_yaml({"scope_domains": ["z.com", "a.com", "m.com"]})
        out = tmp_path / "scope_allowed.json"
        r1 = generate_scope_allowed_json(src, out)
        r2 = generate_scope_allowed_json(src, out)
        assert r1.patterns == r2.patterns == ["z.com", "a.com", "m.com"]

    def test_propagates_scope_config_error(self, tmp_path):
        with pytest.raises(ScopeConfigError):
            generate_scope_allowed_json(tmp_path / "missing.yaml", tmp_path / "out.json")
