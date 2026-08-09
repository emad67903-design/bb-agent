"""
Implements: Section 3 / Section 4.4 test coverage -- scope_enforcer.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest
import yaml

from core.governance.scope_config_generator import ScopeConfigError
from core.governance.scope_enforcer import (
    _is_scope_allowed,
    is_allowed,
    load_scope_domains_for_enforcement,
)
from core.ontology.scope import CredentialValidationAllowlist


@pytest.fixture
def scope_yaml(tmp_path):
    def _write(content: dict):
        path = tmp_path / "scope.yaml"
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        return path

    return _write


class TestIsScopeAllowed:
    """Section 4.4's wildcard-matching rule, transcribed verbatim."""

    def test_exact_match(self):
        assert _is_scope_allowed("example.com", ["example.com"]) is True

    def test_wildcard_matches_base_domain(self):
        assert _is_scope_allowed("example.com", ["*.example.com"]) is True

    def test_wildcard_matches_subdomain(self):
        assert _is_scope_allowed("sub.example.com", ["*.example.com"]) is True

    def test_wildcard_matches_nested_subdomain(self):
        assert _is_scope_allowed("deep.sub.example.com", ["*.example.com"]) is True

    def test_no_match_returns_false(self):
        assert _is_scope_allowed("evil.com", ["example.com", "*.other.com"]) is False

    def test_wildcard_does_not_match_unrelated_suffix(self):
        """'notexample.com' must not match '*.example.com' -- the check
        is a dot-boundary suffix match, not a bare string suffix."""
        assert _is_scope_allowed("notexample.com", ["*.example.com"]) is False

    def test_none_host_returns_false(self):
        """An unparseable URL's `.hostname` is `None` -- must fail
        closed, not raise or match anything."""
        assert _is_scope_allowed(None, ["example.com"]) is False

    def test_empty_pattern_list_rejects_everything(self):
        assert _is_scope_allowed("example.com", []) is False


class TestIsAllowed:
    """Section 4.4's `is_allowed(url, caller_id=...)`, v6.5/V6.4-M2:
    explicit parameter, not call-stack inspection."""

    def test_in_scope_url_allowed(self):
        assert is_allowed("https://example.com/path", ["*.example.com"]) is True

    def test_out_of_scope_url_rejected(self):
        assert is_allowed("https://evil.com/path", ["*.example.com"]) is False

    def test_subdomain_in_scope_via_wildcard(self):
        assert is_allowed("https://api.example.com/v1/users", ["*.example.com"]) is True

    def test_malformed_url_fails_closed_not_raises(self):
        """No hostname parses out of this -- must return False, never
        raise, so a caller's scope check can't itself become a crash
        vector on attacker-influenced input."""
        assert is_allowed("not a url at all", ["example.com"]) is False

    def test_caller_id_alone_without_an_allowlist_still_grants_nothing(self):
        """Week 6 (docs/DECISIONS.md item 65) update: the allowlist
        mechanism now exists, but this call doesn't provide one --
        `credential_validation_allowlist` defaults to `None`, and the
        exemption branch must fail closed on that, not raise and not
        silently assume some allowlist. This is exactly what every Week
        3/5 caller (browser_tool.py, network_observer.py,
        rate_limited_client.py) still does today -- none of them pass
        this parameter, so the exemption can never fire through any of
        them."""
        assert (
            is_allowed(
                "https://sts.amazonaws.com/",
                ["*.example.com"],
                caller_id="hardcoded_credentials",
            )
            is False
        )

    def test_caller_id_defaults_to_none(self):
        assert is_allowed("https://example.com/", ["example.com"]) is True


class TestCredentialValidationAllowlistExemption:
    """Week 6 (docs/DECISIONS.md item 65): Section 4.4's exemption
    branch, verbatim -- `caller_id == "hardcoded_credentials"` AND
    `credential_validation_allowlist.enabled` AND `host in
    credential_validation_allowlist.external_apis`."""

    @pytest.fixture
    def allowlist(self):
        return CredentialValidationAllowlist(
            enabled=True,
            external_apis=["sts.amazonaws.com", "api.stripe.com"],
        )

    def test_all_three_conditions_met_grants_access(self, allowlist):
        assert (
            is_allowed(
                "https://sts.amazonaws.com/latest/api/token",
                ["*.example.com"],
                caller_id="hardcoded_credentials",
                credential_validation_allowlist=allowlist,
            )
            is True
        )

    def test_wrong_caller_id_denies_even_with_valid_allowlist(self, allowlist):
        """The exemption is scanner-specific -- Section 4.4 permits it
        for hardcoded_credentials.py only. Any other caller_id,
        including a plausible-looking scanner name, must not reach an
        otherwise-matching allowlist."""
        assert (
            is_allowed(
                "https://sts.amazonaws.com/",
                ["*.example.com"],
                caller_id="ssrf_scanner",
                credential_validation_allowlist=allowlist,
            )
            is False
        )

    def test_disabled_allowlist_denies_even_with_correct_caller_and_host(self):
        """scope.yaml's own comment: 'set false -> skip external
        validation; mark TIER_D for human'. enabled is a hard kill
        switch, independent of what's in external_apis."""
        disabled = CredentialValidationAllowlist(
            enabled=False,
            external_apis=["sts.amazonaws.com"],
        )
        assert (
            is_allowed(
                "https://sts.amazonaws.com/",
                ["*.example.com"],
                caller_id="hardcoded_credentials",
                credential_validation_allowlist=disabled,
            )
            is False
        )

    def test_host_not_in_external_apis_denies(self, allowlist):
        assert (
            is_allowed(
                "https://not-on-the-list.com/",
                ["*.example.com"],
                caller_id="hardcoded_credentials",
                credential_validation_allowlist=allowlist,
            )
            is False
        )

    def test_matching_is_exact_not_wildcard_aware(self, allowlist):
        """Deliberately different from _is_scope_allowed's wildcard
        matching (Section 4.4's `host in external_apis` is a plain
        containment check against a literal list, not a *.-aware scan)
        -- a subdomain of an allowlisted host must NOT match."""
        assert (
            is_allowed(
                "https://evil.sts.amazonaws.com/",
                ["*.example.com"],
                caller_id="hardcoded_credentials",
                credential_validation_allowlist=allowlist,
            )
            is False
        )

    def test_in_scope_url_short_circuits_before_the_exemption_is_even_considered(self, allowlist):
        """The exemption is a fallback, not a precondition -- an
        in-scope URL is allowed on the ordinary scope check regardless
        of caller_id or allowlist contents."""
        assert (
            is_allowed(
                "https://api.example.com/",
                ["*.example.com"],
                caller_id="some_other_caller",
                credential_validation_allowlist=CredentialValidationAllowlist(
                    enabled=False, external_apis=[]
                ),
            )
            is True
        )

    def test_none_allowlist_with_matching_caller_id_fails_closed(self):
        """Redundant with TestIsAllowed's own coverage of this exact
        case, kept here too since it is this test class's core
        boundary: caller_id alone, without an allowlist object, must
        never be sufficient."""
        assert (
            is_allowed(
                "https://sts.amazonaws.com/",
                ["*.example.com"],
                caller_id="hardcoded_credentials",
                credential_validation_allowlist=None,
            )
            is False
        )


class TestLoadScopeDomainsForEnforcement:
    """Delegation to scope_config_generator.load_scope_domains -- same
    single-source-of-truth YAML reader, not a second parser."""

    def test_delegates_successfully(self, scope_yaml):
        path = scope_yaml({"scope_domains": ["*.example.com", "example.com"]})
        assert load_scope_domains_for_enforcement(path) == ["*.example.com", "example.com"]

    def test_delegates_errors_unchanged(self, tmp_path):
        with pytest.raises(ScopeConfigError, match="not found"):
            load_scope_domains_for_enforcement(tmp_path / "does_not_exist.yaml")


class TestEndToEndAgainstRealScopeYaml:
    """Sanity check against this repo's actual configs/scope.yaml, not
    just synthetic fixtures."""

    def test_real_scope_yaml_loads_and_enforces(self):
        from pathlib import Path

        real_path = Path(__file__).parent.parent.parent.parent / "configs" / "scope.yaml"
        domains = load_scope_domains_for_enforcement(real_path)
        assert domains, "configs/scope.yaml must declare at least one scope_domains entry"
        assert is_allowed("https://evil-out-of-scope-host.test/", domains) is False
