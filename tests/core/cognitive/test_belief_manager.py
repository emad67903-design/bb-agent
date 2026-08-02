"""
Implements: Section 11 test coverage -- core/cognitive/belief_manager.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import networkx as nx
import pytest

import core.cognitive.belief_manager as belief_manager
from core.cognitive.belief_manager import (
    BELIEF_GRAPH_LIMITS,
    NEVER_PRUNE_EXPLOITABILITY_THRESHOLD,
    PRUNE_LOW_PROBABILITY_STALE_MINUTES,
    PRUNE_MODERATE_PROBABILITY_THRESHOLD,
    PRUNE_MODERATE_STALE_MINUTES,
    add_belief_node,
    deserialize_belief_graph,
    get_probability,
    new_belief_graph,
    prune_graph,
    seed_node,
    serialize_belief_graph,
    should_prune,
    update_node,
)
from core.ontology.enums import BusinessValue

_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def _minutes_ago(n: float, base: datetime = _NOW) -> datetime:
    return base - timedelta(minutes=n)


class TestBeliefGraphLimits:
    def test_matches_section_11_1_exactly(self):
        assert BELIEF_GRAPH_LIMITS == {
            "max_nodes": 15_000,
            "max_edges": 100_000,
            "pruning_threshold": 0.05,
            "pruning_interval": 300,
        }


class TestSeedNode:
    def test_matches_section_11_2_worked_example(self):
        """Pinned per this week's kickoff instructions: w=0.65 -> alpha=7,
        beta=3, mean=0.70 ~ the 0.65 prior."""
        result = seed_node("xss", 0.65)
        assert result == {"alpha": 7, "beta": 3}
        assert result["alpha"] / (result["alpha"] + result["beta"]) == pytest.approx(0.70)

    def test_regression_python_builtin_round_would_give_wrong_answer(self):
        """Explicit regression pin for docs/DECISIONS.md item 46: fails
        loudly if `seed_node` is ever "simplified" back to Python's
        built-in `round()`, which silently gives {'alpha': 6, 'beta': 4}
        for this exact input (banker's rounding: round(6.5) == 6)."""
        assert round(0.65 * 10) == 6, "sanity check on Python's own round() behavior changed"
        assert seed_node("xss", 0.65) != {"alpha": 6, "beta": 4}
        assert seed_node("xss", 0.65) == {"alpha": 7, "beta": 3}

    @pytest.mark.parametrize(
        "w,expected_alpha",
        [
            (0.20, 2), (0.25, 3), (0.30, 3), (0.35, 4), (0.40, 4),
            (0.45, 5), (0.50, 5), (0.55, 6), (0.60, 6), (0.65, 7),
        ],
    )
    def test_every_vuln_weights_yaml_starting_weight_rounds_half_up(self, w, expected_alpha):
        """All ten distinct `starting_weights` values actually used in
        Section 9.5's `vuln_weights.yaml`, round-half-up (docs/DECISIONS.md
        item 46) -- three of these (0.25, 0.45, 0.65) would silently
        mis-seed under Python's built-in `round()`."""
        assert seed_node("t", w) == {"alpha": expected_alpha, "beta": 10 - expected_alpha}

    def test_alpha_beta_always_sum_to_ten(self):
        for w in [0.0, 0.05, 0.15, 0.5, 0.85, 0.95, 1.0]:
            result = seed_node("t", w)
            assert result["alpha"] + result["beta"] == 10

    def test_vuln_type_accepted_but_unused_in_arithmetic(self):
        """Section 11.2's own signature takes vuln_type but the given
        one-line body never references it (module docstring)."""
        assert seed_node("xss", 0.5) == seed_node("sqli", 0.5) == seed_node("literally_anything", 0.5)

    def test_return_values_are_int_not_float(self):
        result = seed_node("t", 0.65)
        assert isinstance(result["alpha"], int) and isinstance(result["beta"], int)


class TestNewBeliefGraph:
    def test_returns_empty_digraph(self):
        g = new_belief_graph()
        assert isinstance(g, nx.DiGraph)
        assert g.number_of_nodes() == 0


class TestAddBeliefNode:
    def test_populates_all_nine_section_11_2_attributes(self):
        g = new_belief_graph()
        add_belief_node(
            g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
            starting_weight=0.65, business_value=BusinessValue.HIGH, now=_NOW,
        )
        attrs = dict(g.nodes["h1"])
        assert set(attrs.keys()) == {
            "hypothesis_id", "vuln_type", "endpoint", "alpha", "beta",
            "exploitability_score", "business_value", "pinned_until", "last_updated",
        }
        assert attrs["hypothesis_id"] == "h1"
        assert attrs["vuln_type"] == "xss"
        assert attrs["endpoint"] == "/a"
        assert attrs["alpha"] == 7
        assert attrs["beta"] == 3
        assert attrs["exploitability_score"] == 0.5
        assert attrs["business_value"] is BusinessValue.HIGH
        assert attrs["pinned_until"] is None
        assert attrs["last_updated"] == _NOW

    def test_exploitability_score_defaults_to_point_five(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.5, business_value=BusinessValue.LOW, now=_NOW)
        assert g.nodes["h1"]["exploitability_score"] == 0.5

    def test_exploitability_score_overridable(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.5, business_value=BusinessValue.LOW,
                         exploitability_score=0.9, now=_NOW)
        assert g.nodes["h1"]["exploitability_score"] == 0.9

    def test_business_value_has_no_default_and_is_keyword_only(self):
        """docs/DECISIONS.md item 45: UNKNOWN is reserved for the
        deserialization fallback only, so no default is offered here --
        omitting business_value is a TypeError, not a silent UNKNOWN."""
        g = new_belief_graph()
        with pytest.raises(TypeError):
            add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a", starting_weight=0.5)  # type: ignore[call-arg]

    def test_returns_hypothesis_id(self):
        g = new_belief_graph()
        result = add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                                  starting_weight=0.5, business_value=BusinessValue.LOW, now=_NOW)
        assert result == "h1"

    def test_uses_seed_node_internally_not_reimplemented_arithmetic(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.65, business_value=BusinessValue.LOW, now=_NOW)
        expected = seed_node("xss", 0.65)
        assert g.nodes["h1"]["alpha"] == expected["alpha"]
        assert g.nodes["h1"]["beta"] == expected["beta"]


class TestUpdateNode:
    def _graph_with_node(self) -> nx.DiGraph:
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.5, business_value=BusinessValue.LOW, now=_minutes_ago(5))
        return g

    def test_success_increments_alpha_only(self):
        g = self._graph_with_node()
        before = dict(g.nodes["h1"])
        update_node(g, "h1", success=True)
        assert g.nodes["h1"]["alpha"] == before["alpha"] + 1
        assert g.nodes["h1"]["beta"] == before["beta"]

    def test_failure_increments_beta_only(self):
        g = self._graph_with_node()
        before = dict(g.nodes["h1"])
        update_node(g, "h1", success=False)
        assert g.nodes["h1"]["beta"] == before["beta"] + 1
        assert g.nodes["h1"]["alpha"] == before["alpha"]

    def test_refreshes_last_updated(self):
        """update_node has no injectable `now` (Section 11.2's literal
        signature has none, unlike add_belief_node/prune_graph), so it
        always stamps real wall-clock time -- this test anchors against
        real time via a fixed past date, not the fictional _NOW fixture
        (which sits in the future relative to whenever tests actually
        run, and so is unsuitable as a 'before' anchor here)."""
        g = new_belief_graph()
        genuinely_past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.5, business_value=BusinessValue.LOW, now=genuinely_past)
        update_node(g, "h1", success=True)
        assert g.nodes["h1"]["last_updated"] > genuinely_past

    def test_last_updated_is_timezone_aware(self):
        g = self._graph_with_node()
        update_node(g, "h1", success=True)
        assert g.nodes["h1"]["last_updated"].tzinfo is not None


class TestGetProbability:
    def test_matches_seed_mean_immediately_after_seeding(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.65, business_value=BusinessValue.LOW, now=_NOW)
        assert get_probability(g, "h1") == pytest.approx(0.70)

    def test_updates_shift_probability(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.5, business_value=BusinessValue.LOW, now=_NOW)
        assert get_probability(g, "h1") == pytest.approx(0.5)
        for _ in range(5):
            update_node(g, "h1", success=True)
        assert get_probability(g, "h1") == pytest.approx(10 / 15)

    def test_never_divides_by_zero_across_many_updates(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.0, business_value=BusinessValue.LOW, now=_NOW)
        for _ in range(50):
            update_node(g, "h1", success=False)
        # alpha starts at 0, beta at 10; a+b only grows -- never zero.
        assert get_probability(g, "h1") >= 0.0


class TestShouldPrune:
    def _node(self, graph, *, probability_alpha_beta, age_minutes, exploitability_score=0.5, pinned_until=None):
        alpha, beta = probability_alpha_beta
        graph.add_node(
            "h1", hypothesis_id="h1", vuln_type="xss", endpoint="/a",
            alpha=alpha, beta=beta, exploitability_score=exploitability_score,
            business_value=BusinessValue.LOW, pinned_until=pinned_until,
            last_updated=_minutes_ago(age_minutes),
        )

    def test_rule1_low_probability_and_stale_prunes(self):
        g = new_belief_graph()
        # probability = 1/100 = 0.01 < 0.05 (pruning_threshold); stale >= 10 min
        self._node(g, probability_alpha_beta=(1, 99), age_minutes=PRUNE_LOW_PROBABILITY_STALE_MINUTES)
        assert should_prune(g, "h1", _NOW) is True

    def test_rule1_low_probability_but_not_yet_stale_does_not_prune(self):
        g = new_belief_graph()
        self._node(g, probability_alpha_beta=(1, 99), age_minutes=PRUNE_LOW_PROBABILITY_STALE_MINUTES - 1)
        assert should_prune(g, "h1", _NOW) is False

    def test_rule2_moderate_probability_and_very_stale_prunes(self):
        g = new_belief_graph()
        # probability = 1/10 = 0.10, which is < 0.15 but NOT < 0.05 (so rule 1 can't fire)
        self._node(g, probability_alpha_beta=(1, 9), age_minutes=PRUNE_MODERATE_STALE_MINUTES + 1)
        assert get_probability(g, "h1") == pytest.approx(0.10)
        assert should_prune(g, "h1", _NOW) is True

    def test_rule2_moderate_probability_but_not_stale_enough_does_not_prune(self):
        g = new_belief_graph()
        self._node(g, probability_alpha_beta=(1, 9), age_minutes=PRUNE_MODERATE_STALE_MINUTES)
        assert should_prune(g, "h1", _NOW) is False

    def test_healthy_fresh_node_not_pruned(self):
        g = new_belief_graph()
        self._node(g, probability_alpha_beta=(7, 3), age_minutes=1)
        assert should_prune(g, "h1", _NOW) is False

    def test_high_exploitability_exempts_even_when_otherwise_pruneable(self):
        g = new_belief_graph()
        self._node(
            g, probability_alpha_beta=(1, 99), age_minutes=1000,
            exploitability_score=NEVER_PRUNE_EXPLOITABILITY_THRESHOLD + 0.01,
        )
        assert should_prune(g, "h1", _NOW) is False

    def test_exploitability_exactly_at_threshold_does_not_exempt(self):
        """Section 11.2: '> 0.8', strictly greater -- 0.8 itself is not exempt."""
        g = new_belief_graph()
        self._node(
            g, probability_alpha_beta=(1, 99), age_minutes=1000,
            exploitability_score=NEVER_PRUNE_EXPLOITABILITY_THRESHOLD,
        )
        assert should_prune(g, "h1", _NOW) is True

    def test_pinned_future_exempts_even_when_otherwise_pruneable(self):
        g = new_belief_graph()
        self._node(
            g, probability_alpha_beta=(1, 99), age_minutes=1000,
            pinned_until=_NOW + timedelta(hours=1),
        )
        assert should_prune(g, "h1", _NOW) is False

    def test_pinned_in_the_past_does_not_exempt(self):
        """Section 11.2: 'pinned_until > utcnow()' -- a pin that has
        already expired provides no exemption."""
        g = new_belief_graph()
        self._node(
            g, probability_alpha_beta=(1, 99), age_minutes=1000,
            pinned_until=_NOW - timedelta(hours=1),
        )
        assert should_prune(g, "h1", _NOW) is True


class TestPruneGraph:
    def test_removes_only_matching_nodes_from_mixed_graph(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="healthy", vuln_type="xss", endpoint="/a",
                         starting_weight=0.9, business_value=BusinessValue.HIGH, now=_NOW)
        add_belief_node(g, hypothesis_id="stale_and_weak", vuln_type="sqli", endpoint="/b",
                         starting_weight=0.0, business_value=BusinessValue.LOW,
                         now=_minutes_ago(PRUNE_LOW_PROBABILITY_STALE_MINUTES, base=_NOW))
        removed = prune_graph(g, now=_NOW)
        assert removed == ["stale_and_weak"]
        assert set(g.nodes) == {"healthy"}

    def test_returns_empty_list_when_nothing_prunes(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="healthy", vuln_type="xss", endpoint="/a",
                         starting_weight=0.9, business_value=BusinessValue.HIGH, now=_NOW)
        assert prune_graph(g, now=_NOW) == []
        assert g.number_of_nodes() == 1

    def test_defaults_now_when_not_supplied(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                         starting_weight=0.9, business_value=BusinessValue.HIGH)
        # Freshly added with real "now" -- should not prune under a real default "now" either.
        assert prune_graph(g) == []


class TestSerializationRoundTrip:
    def _rich_graph(self) -> nx.DiGraph:
        g = new_belief_graph()
        add_belief_node(
            g, hypothesis_id="h1", vuln_type="xss", endpoint="/a", starting_weight=0.65,
            business_value=BusinessValue.HIGH, exploitability_score=0.83,
            pinned_until=_NOW + timedelta(hours=2), now=_NOW,
        )
        add_belief_node(
            g, hypothesis_id="h2", vuln_type="sqli", endpoint="/b", starting_weight=0.55,
            business_value=BusinessValue.MEDIUM, now=_minutes_ago(30),
        )
        g.add_edge("h1", "h2")
        return g

    def test_uses_links_key_not_edges_key(self):
        """Section 11.3's edges='links' requirement, verified structurally,
        not just by absence of a crash."""
        raw = serialize_belief_graph(self._rich_graph())
        parsed = json.loads(raw)
        assert "links" in parsed
        assert "edges" not in parsed

    def test_full_field_for_field_round_trip(self):
        original = self._rich_graph()
        restored = deserialize_belief_graph(serialize_belief_graph(original))

        assert set(restored.nodes) == set(original.nodes)
        assert set(restored.edges) == set(original.edges)

        for node_id in original.nodes:
            orig_attrs = dict(original.nodes[node_id])
            new_attrs = dict(restored.nodes[node_id])
            assert orig_attrs == new_attrs, f"mismatch on node {node_id}"

    def test_alpha_beta_are_int_not_float_after_round_trip(self):
        restored = deserialize_belief_graph(serialize_belief_graph(self._rich_graph()))
        for node_id in restored.nodes:
            alpha = restored.nodes[node_id]["alpha"]
            beta = restored.nodes[node_id]["beta"]
            assert type(alpha) is int, f"{node_id}: alpha is {type(alpha)}, not int"
            assert type(beta) is int, f"{node_id}: beta is {type(beta)}, not int"

    def test_business_value_is_businessvalue_instance_after_round_trip(self):
        restored = deserialize_belief_graph(serialize_belief_graph(self._rich_graph()))
        assert restored.nodes["h1"]["business_value"] is BusinessValue.HIGH
        assert restored.nodes["h2"]["business_value"] is BusinessValue.MEDIUM

    def test_datetimes_remain_timezone_aware_after_round_trip(self):
        restored = deserialize_belief_graph(serialize_belief_graph(self._rich_graph()))
        assert restored.nodes["h1"]["last_updated"].tzinfo is not None
        assert restored.nodes["h1"]["pinned_until"].tzinfo is not None
        assert restored.nodes["h2"]["pinned_until"] is None

    def test_none_pinned_until_preserved_not_coerced_to_string(self):
        restored = deserialize_belief_graph(serialize_belief_graph(self._rich_graph()))
        assert restored.nodes["h2"]["pinned_until"] is None

    def test_corrupted_business_value_falls_back_to_unknown(self, caplog):
        """docs/DECISIONS.md's R-L4 fix: a corrupted checkpoint value
        recovers to BusinessValue.UNKNOWN with a warning logging the
        ORIGINAL bad value, not 'UNKNOWN' -- and does not raise."""
        g = self._rich_graph()
        raw = serialize_belief_graph(g)
        data = json.loads(raw)
        data["nodes"][0]["business_value"] = "not_a_real_business_value"
        corrupted_raw = json.dumps(data)

        with caplog.at_level("WARNING", logger="core.cognitive.belief_manager"):
            restored = deserialize_belief_graph(corrupted_raw)

        assert restored.nodes["h1"]["business_value"] is BusinessValue.UNKNOWN
        assert any("not_a_real_business_value" in record.message for record in caplog.records)
        assert not any("BusinessValue.UNKNOWN" in record.message for record in caplog.records)

    def test_corrupted_business_value_does_not_raise(self):
        g = self._rich_graph()
        data = json.loads(serialize_belief_graph(g))
        data["nodes"][0]["business_value"] = "garbage"
        # Must not raise ValueError -- the whole point of the R-L4 fix.
        deserialize_belief_graph(json.dumps(data))

    def test_missing_business_value_key_is_left_alone(self):
        """The blueprint's `if 'business_value' in node:` guard means a
        node with no business_value key at all is passed through
        untouched, not defaulted to UNKNOWN."""
        g = new_belief_graph()
        g.add_node("h1", hypothesis_id="h1", vuln_type="xss", endpoint="/a",
                   alpha=5, beta=5, exploitability_score=0.5,
                   pinned_until=None, last_updated=_NOW)
        restored = deserialize_belief_graph(serialize_belief_graph(g))
        assert "business_value" not in restored.nodes["h1"]

    def test_estimated_json_size_is_reasonable_for_a_small_graph(self):
        """Sanity check against Section 11.3's '~25 MB for 15,000 nodes +
        100,000 edges' estimate -- not a hard assertion on the estimate
        itself, just confirming serialization doesn't blow up wildly for
        a small graph."""
        raw = serialize_belief_graph(self._rich_graph())
        assert len(raw.encode("utf-8")) < 5_000  # 2 nodes + 1 edge should be tiny


class TestIntegration:
    def test_full_lifecycle_seed_update_prune_serialize_deserialize(self):
        g = new_belief_graph()
        add_belief_node(g, hypothesis_id="survivor", vuln_type="xss", endpoint="/a",
                         starting_weight=0.9, business_value=BusinessValue.HIGH, now=_NOW)
        add_belief_node(g, hypothesis_id="doomed", vuln_type="sqli", endpoint="/b",
                         starting_weight=0.0, business_value=BusinessValue.LOW,
                         now=_minutes_ago(PRUNE_LOW_PROBABILITY_STALE_MINUTES, base=_NOW))

        update_node(g, "survivor", success=True)
        update_node(g, "survivor", success=True)

        removed = prune_graph(g, now=_NOW)
        assert removed == ["doomed"]

        restored = deserialize_belief_graph(serialize_belief_graph(g))
        assert set(restored.nodes) == {"survivor"}
        assert restored.nodes["survivor"]["alpha"] == g.nodes["survivor"]["alpha"]
        assert get_probability(restored, "survivor") == get_probability(g, "survivor")
