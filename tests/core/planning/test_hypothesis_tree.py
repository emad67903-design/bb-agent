"""
Implements: Section 39 review test coverage -- core/planning/hypothesis_tree.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.cognitive.belief_manager import add_belief_node, new_belief_graph
from core.ontology.enums import BusinessValue
from core.planning.hypothesis_tree import (
    ALTERNATIVE_TO,
    add_alternative_relationship,
    get_alternatives_of,
    is_alternative_of,
)

_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def _seeded_graph(*hypothesis_ids: str):
    """A BeliefGraph with one committed node per given ID, all otherwise
    identical (xss/. + weight 0.5 / MEDIUM), for relationship tests that
    don't care about Bayesian state."""
    graph = new_belief_graph()
    for hid in hypothesis_ids:
        add_belief_node(
            graph,
            hypothesis_id=hid,
            vuln_type="xss",
            endpoint="/a",
            starting_weight=0.5,
            business_value=BusinessValue.MEDIUM,
            now=_NOW,
        )
    return graph


class TestAddAlternativeRelationship:
    def test_adds_edge_from_alternative_to_original(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        assert graph.has_edge("alt", "orig")

    def test_edge_carries_relation_type_and_created_at(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        attrs = graph.edges["alt", "orig"]
        assert attrs["relation_type"] == ALTERNATIVE_TO == "alternative_to"
        assert attrs["created_at"] == _NOW.isoformat()

    def test_relation_type_is_plain_string_not_enum(self):
        # Deliberate (module docstring): keeps belief_manager.py's
        # serializer untouched. A raw enum would fail this assertion.
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        assert isinstance(graph.edges["alt", "orig"]["relation_type"], str)

    def test_defaults_now_when_not_supplied(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig")
        assert graph.edges["alt", "orig"]["created_at"]  # non-empty ISO string

    def test_raises_keyerror_when_alternative_id_missing(self):
        graph = _seeded_graph("orig")
        with pytest.raises(KeyError, match="ghost_alt"):
            add_alternative_relationship(graph, "ghost_alt", "orig", now=_NOW)

    def test_raises_keyerror_when_original_id_missing(self):
        graph = _seeded_graph("alt")
        with pytest.raises(KeyError, match="ghost_orig"):
            add_alternative_relationship(graph, "alt", "ghost_orig", now=_NOW)

    def test_neither_node_created_as_side_effect_of_missing_other(self):
        graph = _seeded_graph("alt")
        with pytest.raises(KeyError):
            add_alternative_relationship(graph, "alt", "ghost_orig", now=_NOW)
        assert graph.number_of_nodes() == 1
        assert not graph.has_node("ghost_orig")


class TestGetAlternativesOf:
    def test_empty_list_when_no_alternatives(self):
        graph = _seeded_graph("orig")
        assert get_alternatives_of(graph, "orig") == []

    def test_empty_list_when_hypothesis_id_absent(self):
        graph = _seeded_graph("orig")
        assert get_alternatives_of(graph, "does_not_exist") == []

    def test_returns_single_alternative(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        assert get_alternatives_of(graph, "orig") == ["alt"]

    def test_returns_multiple_alternatives(self):
        graph = _seeded_graph("alt1", "alt2", "orig")
        add_alternative_relationship(graph, "alt1", "orig", now=_NOW)
        add_alternative_relationship(graph, "alt2", "orig", now=_NOW)
        assert set(get_alternatives_of(graph, "orig")) == {"alt1", "alt2"}

    def test_does_not_return_the_alternative_itself_as_its_own_alternative(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        assert get_alternatives_of(graph, "alt") == []

    def test_ignores_non_alternative_edges(self):
        # An edge present but NOT tagged alternative_to must not be
        # picked up -- get_alternatives_of filters on relation_type,
        # not on bare edge existence.
        graph = _seeded_graph("a", "b")
        graph.add_edge("a", "b", relation_type="something_else")
        assert get_alternatives_of(graph, "b") == []


class TestIsAlternativeOf:
    def test_true_when_relationship_exists(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        assert is_alternative_of(graph, "alt", "orig") is True

    def test_false_when_no_edge(self):
        graph = _seeded_graph("alt", "orig")
        assert is_alternative_of(graph, "alt", "orig") is False

    def test_false_when_edge_exists_reversed(self):
        graph = _seeded_graph("alt", "orig")
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)
        assert is_alternative_of(graph, "orig", "alt") is False

    def test_false_when_either_id_absent(self):
        graph = _seeded_graph("alt")
        assert is_alternative_of(graph, "alt", "nonexistent") is False
        assert is_alternative_of(graph, "nonexistent", "alt") is False

    def test_false_when_edge_exists_but_wrong_relation_type(self):
        graph = _seeded_graph("a", "b")
        graph.add_edge("a", "b", relation_type="something_else")
        assert is_alternative_of(graph, "a", "b") is False


class TestPruningInteraction:
    """Empirically re-pins the review's own claim: pruning a node drops
    its incident relationship edges automatically, via networkx's
    standard remove_nodes_from behavior -- zero code in this module
    participates."""

    def test_pruning_falsified_original_drops_the_relationship_edge(self):
        from core.cognitive.belief_manager import prune_graph

        graph = new_belief_graph()
        stale_low_prob = datetime(2026, 8, 2, 8, 0, 0, tzinfo=timezone.utc)  # 4h before _NOW
        add_belief_node(
            graph, hypothesis_id="orig", vuln_type="xss", endpoint="/a",
            starting_weight=0.05, business_value=BusinessValue.LOW, now=stale_low_prob,
        )
        add_belief_node(
            graph, hypothesis_id="alt", vuln_type="xss", endpoint="/b",
            starting_weight=0.65, business_value=BusinessValue.HIGH, now=_NOW,
        )
        add_alternative_relationship(graph, "alt", "orig", now=_NOW)

        removed = prune_graph(graph, now=_NOW)

        assert "orig" in removed
        assert not graph.has_node("orig")
        assert graph.has_node("alt")
        assert get_alternatives_of(graph, "orig") == []
