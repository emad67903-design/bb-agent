"""
Implements: Section 3 / Section 4.4 test coverage --
core/ontology/scope.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import dataclasses

import pytest

from core.ontology.scope import CredentialValidationAllowlist


class TestCredentialValidationAllowlist:
    def test_holds_enabled_and_external_apis(self):
        a = CredentialValidationAllowlist(
            enabled=True,
            external_apis=["sts.amazonaws.com", "api.stripe.com"],
        )
        assert a.enabled is True
        assert a.external_apis == ["sts.amazonaws.com", "api.stripe.com"]

    def test_disabled_instance(self):
        a = CredentialValidationAllowlist(enabled=False, external_apis=["sts.amazonaws.com"])
        assert a.enabled is False

    def test_empty_external_apis_is_valid(self):
        """Enabled but with nothing listed is a legal state (the
        exemption is switched on but currently matches no host) -- not
        a construction error. Section 4.4's `host in external_apis`
        check simply never matches an empty list."""
        a = CredentialValidationAllowlist(enabled=True, external_apis=[])
        assert a.external_apis == []

    def test_is_frozen(self):
        a = CredentialValidationAllowlist(enabled=True, external_apis=["sts.amazonaws.com"])
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.enabled = False  # type: ignore[misc]

    def test_field_names_match_scope_yaml_keys_and_section_4_4_attribute_access(self):
        """Pins the exact field names Section 4.4's is_allowed()
        pseudocode reads as attributes (`.enabled`, `.external_apis`)
        and configs/scope.yaml uses as its own YAML keys -- no renaming,
        no translation layer between the two."""
        field_names = {f.name for f in dataclasses.fields(CredentialValidationAllowlist)}
        assert field_names == {"enabled", "external_apis"}

    def test_serialization_round_trip(self):
        """Engineering Constitution: every ontology dataclass change
        gets a serialization round-trip test. This type has no custom
        serializer (unlike BeliefGraph's node_link_data-based one) --
        the round trip that matters is dataclasses.asdict() and back,
        since that's the shape load_credential_validation_allowlist()
        constructs it in and the shape any future checkpoint/config
        cache would persist it as."""
        original = CredentialValidationAllowlist(
            enabled=True,
            external_apis=["sts.amazonaws.com", "api.stripe.com", "api.twilio.com"],
        )
        as_dict = dataclasses.asdict(original)
        assert as_dict == {
            "enabled": True,
            "external_apis": ["sts.amazonaws.com", "api.stripe.com", "api.twilio.com"],
        }
        reconstructed = CredentialValidationAllowlist(**as_dict)
        assert reconstructed == original

    def test_equality_is_value_based(self):
        a = CredentialValidationAllowlist(enabled=True, external_apis=["x.com"])
        b = CredentialValidationAllowlist(enabled=True, external_apis=["x.com"])
        assert a == b
