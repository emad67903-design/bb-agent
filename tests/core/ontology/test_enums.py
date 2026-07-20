"""
Implements: Section 3 test coverage -- core/ontology/enums.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest

from core.ontology.enums import PayloadFileType


class TestPayloadFileType:
    def test_has_exactly_two_members(self):
        assert len(PayloadFileType) == 2

    def test_values_match_section_3_1(self):
        assert PayloadFileType.INJECTABLE_PAYLOAD.value == "injectable_payload"
        assert PayloadFileType.PATTERN_LIBRARY.value == "pattern_library"

    def test_string_mixin_serializes_as_plain_value(self):
        assert PayloadFileType.INJECTABLE_PAYLOAD == "injectable_payload"
        assert f"{PayloadFileType.PATTERN_LIBRARY.value}" == "pattern_library"

    def test_constructs_from_string_value(self):
        assert PayloadFileType("injectable_payload") is PayloadFileType.INJECTABLE_PAYLOAD
        assert PayloadFileType("pattern_library") is PayloadFileType.PATTERN_LIBRARY

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            PayloadFileType("not_a_real_type")
