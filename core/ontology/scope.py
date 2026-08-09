"""
Implements: Section 3 -- configs/scope.yaml's `credential_validation_allowlist`
block (R-H4 fix), and Section 4.4's `is_allowed()` exemption branch:
    if (caller_id == "hardcoded_credentials"
            and credential_validation_allowlist.enabled
            and host in credential_validation_allowlist.external_apis):
        return True
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 6 ADDITION (docs/DECISIONS.md item 65): the open question was
whether `credential_validation_allowlist` should be a real type or a
bare dict/tuple. Resolved by the project owner: a real type. Section
4.4's own pseudocode above already settles it independently of
preference -- `.enabled` and `.external_apis` are attribute accesses,
not `dict["enabled"]`/`tuple[1]` indexing, so the blueprint's own author
was already assuming a structured object at the point this exemption
branch was written.

Placed here (`core/ontology/`), not inline in `scope_config_generator.py`
or `scope_enforcer.py`, per the Engineering Constitution's ontology-first
rule ("Every dataclass, enum, and TypedDict is defined ONCE, in
core/ontology/... never invented inline"). New file (`scope.py`), not an
addition to an existing ontology file -- confirmed by the project owner:
nothing existing fits thematically (`browser.py` owns Playwright-capture
types, `enums.py` owns enums, `findings.py` owns the vulnerability/PoC
domain, `http.py` owns HTTP-transport types, `mental_model.py` owns
Phase-2 output) -- `scope.py` pairs naturally with the two files that
produce and consume it, `scope_enforcer.py` and `configs/scope.yaml`.

Exactly two fields, named to match BOTH Section 4.4's attribute-access
names AND `configs/scope.yaml`'s own YAML keys verbatim -- no renaming,
no translation layer between the YAML the person edits and the object
the code reads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialValidationAllowlist:
    """Section 4.4 / R-H4: the `hardcoded_credentials.py`-only exemption
    that lets `core.governance.scope_enforcer.is_allowed()` permit
    read-only GET calls to external credential-validation APIs (AWS STS,
    Stripe, Twilio, Google Maps, Azure/Graph) that sit outside the
    program's own `scope_domains`.

    Loaded from `configs/scope.yaml`'s `credential_validation_allowlist`
    block by
    `core.governance.scope_config_generator.load_credential_validation_allowlist`.
    Consumed by `is_allowed()`'s exemption branch, which grants access
    only when ALL THREE hold: `caller_id == "hardcoded_credentials"`,
    `enabled` is True, and the requested URL's hostname is in
    `external_apis`. No other scanner or component may reach this
    exemption -- `caller_id` is set once, at construction, on the
    calling component's own `RateLimitedClient` instance (Section 4.4),
    never derived from the request itself.

    Attributes:
        enabled: `configs/scope.yaml`'s own comment: "set false ->
            skip external validation; mark TIER_D for human". When
            False, the exemption branch must never fire, regardless of
            caller_id or host -- this is the config-level kill switch
            for the entire exemption, independent of which hosts are
            listed below.
        external_apis: The exact hostnames permitted under this
            exemption (e.g. "sts.amazonaws.com"). Matched by exact
            string equality against the request URL's hostname in
            `is_allowed()` (`host in credential_validation_allowlist.external_apis`,
            Section 4.4, verbatim) -- deliberately NOT wildcard-aware
            like `_is_scope_allowed`'s in-scope check, since
            `configs/scope.yaml`'s comment block lists five fixed,
            named provider hostnames, not a domain-pattern space.
    """

    enabled: bool
    external_apis: list[str]
