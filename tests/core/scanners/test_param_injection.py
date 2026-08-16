"""
Implements: test coverage for core/scanners/param_injection.py
(authored, not blueprint-cited -- see that module's docstring;
docs/DECISIONS.md item 74).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

from core.scanners.param_injection import InjectionPoint, iter_query_param_injections


class TestIterQueryParamInjections:
    def test_no_query_string_returns_empty(self):
        assert iter_query_param_injections("https://x.com/search", "PAYLOAD") == []

    def test_single_param_substituted(self):
        points = iter_query_param_injections("https://x.com/search?q=hello", "PAYLOAD")
        assert points == [InjectionPoint(url="https://x.com/search?q=PAYLOAD", parameter="q")]

    def test_multiple_params_each_substituted_one_at_a_time(self):
        points = iter_query_param_injections("https://x.com/search?q=a&page=2", "PAYLOAD")
        assert len(points) == 2
        assert {p.parameter for p in points} == {"q", "page"}

        by_param = {p.parameter: p.url for p in points}
        # substituting "q" leaves "page" untouched, and vice versa
        assert urllib.parse.parse_qs(urllib.parse.urlsplit(by_param["q"]).query) == {
            "q": ["PAYLOAD"],
            "page": ["2"],
        }
        assert urllib.parse.parse_qs(urllib.parse.urlsplit(by_param["page"]).query) == {
            "q": ["a"],
            "page": ["PAYLOAD"],
        }

    def test_path_scheme_netloc_fragment_preserved(self):
        points = iter_query_param_injections("https://x.com:8443/api/search?q=a#section", "P")
        assert points[0].url == "https://x.com:8443/api/search?q=P#section"

    def test_special_characters_in_payload_round_trip_correctly(self):
        payload = "' OR 1=1-- <script>alert(1)</script>"
        points = iter_query_param_injections("https://x.com/search?q=a", payload)
        recovered = urllib.parse.parse_qs(urllib.parse.urlsplit(points[0].url).query)
        assert recovered["q"] == [payload]

    def test_blank_value_parameter_is_still_a_valid_injection_point(self):
        """keep_blank_values=True: `?q=` (present, empty) is a real,
        testable parameter -- distinct from "no query string at all"."""
        points = iter_query_param_injections("https://x.com/search?q=", "PAYLOAD")
        assert points == [InjectionPoint(url="https://x.com/search?q=PAYLOAD", parameter="q")]

    def test_result_is_immutable(self):
        point = iter_query_param_injections("https://x.com/search?q=a", "P")[0]
        try:
            point.url = "changed"  # type: ignore[misc]
            assert False, "InjectionPoint should be frozen"
        except AttributeError:
            pass
