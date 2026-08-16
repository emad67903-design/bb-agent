"""
Implements: Section 3 test coverage -- core/ontology/surface.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from core.ontology.surface import EndpointSignals


class TestEndpointSignals:
    """docs/DECISIONS.md item 70."""

    def test_constructs_with_only_endpoint_required(self):
        e = EndpointSignals(endpoint="/search")
        assert e.endpoint == "/search"

    def test_signal_counts_defaults_to_empty_dict(self):
        e = EndpointSignals(endpoint="/search")
        assert e.signal_counts == {}

    def test_signal_counts_accepts_a_prepopulated_mapping(self):
        e = EndpointSignals(endpoint="/search", signal_counts={"xss": 4, "sqli": 1})
        assert e.signal_counts == {"xss": 4, "sqli": 1}

    def test_signal_counts_is_mutable_after_construction(self):
        """No mutator method exists on this class deliberately (module
        docstring) -- Fast Lane orchestration (unbuilt) is expected to
        increment counts directly on the dict, the same way
        `belief_manager.py`'s functions mutate a caller-owned
        `networkx.DiGraph` externally rather than the node object
        exposing its own methods."""
        e = EndpointSignals(endpoint="/search")
        e.signal_counts["xss"] = e.signal_counts.get("xss", 0) + 1
        e.signal_counts["xss"] = e.signal_counts.get("xss", 0) + 1
        assert e.signal_counts == {"xss": 2}

    def test_two_instances_do_not_share_the_same_default_dict(self):
        """The classic mutable-default-argument class of bug --
        field(default_factory=dict) must re-evaluate per instance, not
        share one dict across every EndpointSignals ever constructed."""
        e1 = EndpointSignals(endpoint="/search")
        e2 = EndpointSignals(endpoint="/login")
        e1.signal_counts["xss"] = 1
        assert e2.signal_counts == {}

    def test_has_no_passes_signal_gate_method_or_property(self):
        """Pins the module docstring's deliberate omission: the real
        gate-threshold comparison needs `core.verifier.evidence_chain.
        VulnThresholds.min_signals_for`, which this ontology type must
        not import (dependency-direction rule, docs/DECISIONS.md item
        13's precedent). If this is ever added, it should be a
        conscious, cited decision, not an incidental one -- same
        pinning pattern already used by
        `test_base_scanner.py::test_scan_is_abstract_and_declared_on_base_scanner`'s
        predecessor for exactly this reason."""
        assert not hasattr(EndpointSignals, "passes_signal_gate")

    def test_fields_are_exactly_endpoint_and_signal_counts(self):
        """Pins the deliberately minimal shape (module docstring: "kept
        deliberately minimal... nothing speculative beyond it")."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(EndpointSignals)}
        assert field_names == {"endpoint", "signal_counts"}

    def test_survives_a_dict_round_trip(self):
        """Serialization round-trip check (Engineering Constitution).
        No datetime fields here (unlike ExploitCandidate), so this is a
        simpler round-trip than item 69's -- included anyway for the
        same reason: this project's own prior BeliefGraph serialization
        bug is exactly the class of bug this rule exists to catch
        early, and a dict[str, int] value is not itself guaranteed
        JSON-safe without a check (e.g. if a non-str/int key or value
        crept in)."""
        import dataclasses
        import json

        e = EndpointSignals(endpoint="/search", signal_counts={"xss": 4, "sqli": 1})
        as_json = json.dumps(dataclasses.asdict(e))
        restored = EndpointSignals(**json.loads(as_json))
        assert restored == e
