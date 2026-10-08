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
    load_credential_validation_allowlist,
    load_program_type,
    load_race_parallel,
    load_scope_domains,
)
from core.ontology.scope import CredentialValidationAllowlist


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


class TestLoadProgramType:
    """Section 3's scope.yaml comment block (R-L5 fix): program_type
    determines the max autonomous tier -- consumed by
    core/governance/safety_gate.py's VDP enforcement."""

    def test_loads_bug_bounty(self, scope_yaml):
        path = scope_yaml({"program_type": "bug_bounty", "scope_domains": ["a.com"]})
        assert load_program_type(path) == "bug_bounty"

    def test_loads_vdp(self, scope_yaml):
        path = scope_yaml({"program_type": "vdp", "scope_domains": ["a.com"]})
        assert load_program_type(path) == "vdp"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ScopeConfigError, match="not found"):
            load_program_type(tmp_path / "does_not_exist.yaml")

    def test_missing_program_type_key_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"]})
        with pytest.raises(ScopeConfigError, match="program_type"):
            load_program_type(path)

    def test_invalid_program_type_value_raises_fail_closed(self, scope_yaml):
        """Section-cited fail-closed behavior: an unrecognized value must
        not silently default to either bug_bounty's or vdp's cap."""
        path = scope_yaml({"program_type": "enterprise_pentest", "scope_domains": ["a.com"]})
        with pytest.raises(ScopeConfigError, match="bug_bounty"):
            load_program_type(path)

    def test_malformed_yaml_raises(self, tmp_path):
        path = tmp_path / "scope.yaml"
        path.write_text("program_type: [unclosed", encoding="utf-8")
        with pytest.raises(ScopeConfigError, match="not valid YAML"):
            load_program_type(path)


class TestLoadCredentialValidationAllowlist:
    """Week 6 (docs/DECISIONS.md item 65): Section 3's scope.yaml
    comment block (R-H4 fix) -- consumed by
    core/governance/scope_enforcer.py's is_allowed() exemption branch."""

    def test_loads_enabled_and_external_apis(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {
                    "enabled": True,
                    "external_apis": ["sts.amazonaws.com", "api.stripe.com"],
                },
            }
        )
        result = load_credential_validation_allowlist(path)
        assert result == CredentialValidationAllowlist(
            enabled=True,
            external_apis=["sts.amazonaws.com", "api.stripe.com"],
        )

    def test_loads_disabled(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": False, "external_apis": []},
            }
        )
        result = load_credential_validation_allowlist(path)
        assert result.enabled is False

    def test_empty_external_apis_is_valid(self, scope_yaml):
        """enabled: true with nothing listed is a legal config state,
        not an error -- see core/ontology/scope.py's own test for the
        same point at the dataclass level."""
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": True, "external_apis": []},
            }
        )
        result = load_credential_validation_allowlist(path)
        assert result.external_apis == []

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ScopeConfigError, match="not found"):
            load_credential_validation_allowlist(tmp_path / "does_not_exist.yaml")

    def test_missing_credential_validation_allowlist_key_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"]})
        with pytest.raises(ScopeConfigError, match="credential_validation_allowlist"):
            load_credential_validation_allowlist(path)

    def test_block_not_a_mapping_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"], "credential_validation_allowlist": "not-a-mapping"})
        with pytest.raises(ScopeConfigError, match="mapping"):
            load_credential_validation_allowlist(path)

    def test_missing_enabled_key_raises(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"external_apis": ["a.com"]},
            }
        )
        with pytest.raises(ScopeConfigError, match="enabled"):
            load_credential_validation_allowlist(path)

    def test_non_bool_enabled_raises(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": "yes", "external_apis": ["a.com"]},
            }
        )
        with pytest.raises(ScopeConfigError, match="boolean"):
            load_credential_validation_allowlist(path)

    def test_missing_external_apis_key_raises(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": True},
            }
        )
        with pytest.raises(ScopeConfigError, match="external_apis"):
            load_credential_validation_allowlist(path)

    def test_non_list_external_apis_raises(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": True, "external_apis": "sts.amazonaws.com"},
            }
        )
        with pytest.raises(ScopeConfigError, match="external_apis"):
            load_credential_validation_allowlist(path)

    def test_non_string_entry_in_external_apis_raises(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": True, "external_apis": ["a.com", 123]},
            }
        )
        with pytest.raises(ScopeConfigError, match="external_apis"):
            load_credential_validation_allowlist(path)

    def test_empty_string_entry_in_external_apis_raises(self, scope_yaml):
        path = scope_yaml(
            {
                "scope_domains": ["a.com"],
                "credential_validation_allowlist": {"enabled": True, "external_apis": ["a.com", "  "]},
            }
        )
        with pytest.raises(ScopeConfigError, match="external_apis"):
            load_credential_validation_allowlist(path)

    def test_malformed_yaml_raises(self, tmp_path):
        path = tmp_path / "scope.yaml"
        path.write_text("credential_validation_allowlist: [unclosed", encoding="utf-8")
        with pytest.raises(ScopeConfigError, match="not valid YAML"):
            load_credential_validation_allowlist(path)

    def test_real_scope_yaml_loads_the_five_documented_providers(self):
        """Sanity check against this repo's actual configs/scope.yaml,
        not just synthetic fixtures -- Section 3's comment block names
        exactly these five hosts."""
        from pathlib import Path

        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "scope.yaml"
        result = load_credential_validation_allowlist(real_path)
        assert result.enabled is True
        assert result.external_apis == [
            "sts.amazonaws.com",
            "api.stripe.com",
            "api.twilio.com",
            "maps.googleapis.com",
            "graph.microsoft.com",
        ]


class TestLoadRaceParallel:
    """Week 7 Batch 5 (docs/DECISIONS.md item 106): Section 3's
    scope.yaml comment block (R-L7 fix) -- consumed by
    core/scanners/race_scanner.py's constructor as its production
    default (race_parallel is NOT in config.py)."""

    def test_loads_positive_integer(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"], "race_parallel": 30})
        assert load_race_parallel(path) == 30

    def test_loads_a_different_positive_integer(self, scope_yaml):
        """Not just echoing the blueprint's own example value (30) --
        confirms the function reads the real field, not a hardcoded
        constant disguised as a loader."""
        path = scope_yaml({"scope_domains": ["a.com"], "race_parallel": 7})
        assert load_race_parallel(path) == 7

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ScopeConfigError, match="not found"):
            load_race_parallel(tmp_path / "does_not_exist.yaml")

    def test_missing_race_parallel_key_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"]})
        with pytest.raises(ScopeConfigError, match="race_parallel"):
            load_race_parallel(path)

    def test_zero_raises_fail_closed(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"], "race_parallel": 0})
        with pytest.raises(ScopeConfigError, match="positive integer"):
            load_race_parallel(path)

    def test_negative_raises_fail_closed(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"], "race_parallel": -5})
        with pytest.raises(ScopeConfigError, match="positive integer"):
            load_race_parallel(path)

    def test_bool_raises_despite_being_an_int_subclass(self, scope_yaml):
        """Python's bool is an int subclass (isinstance(True, int) is
        True) -- a YAML `race_parallel: true` must not silently become
        `race_parallel: 1`."""
        path = scope_yaml({"scope_domains": ["a.com"], "race_parallel": True})
        with pytest.raises(ScopeConfigError, match="positive integer"):
            load_race_parallel(path)

    def test_non_int_raises(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["a.com"], "race_parallel": "thirty"})
        with pytest.raises(ScopeConfigError, match="positive integer"):
            load_race_parallel(path)

    def test_malformed_yaml_raises(self, tmp_path):
        path = tmp_path / "scope.yaml"
        path.write_text("race_parallel: [unclosed", encoding="utf-8")
        with pytest.raises(ScopeConfigError, match="not valid YAML"):
            load_race_parallel(path)

    def test_real_scope_yaml_loads_thirty(self):
        """Sanity check against this repo's actual configs/scope.yaml --
        Section 3's comment block's own documented default."""
        from pathlib import Path

        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "scope.yaml"
        assert load_race_parallel(real_path) == 30


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
