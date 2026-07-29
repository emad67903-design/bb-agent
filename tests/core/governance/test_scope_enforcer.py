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

    def test_caller_id_accepted_but_inert_this_week(self):
        """Week 6 scope (docs/DECISIONS.md item 31): passing any
        caller_id, including the eventual 'hardcoded_credentials'
        value, must not grant access this week -- there is no
        allowlist to check against yet, and this function must not
        silently invent one."""
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
