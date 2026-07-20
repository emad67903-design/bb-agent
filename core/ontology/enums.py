"""
Implements: Section 3 -- core/ontology/enums.py (PayloadFileType only)
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 0 SCOPE NOTE: Section 3 names eleven enums for this module
(FailureCause, RiskLevel, WorkflowConfidence, HumanFeedback,
MemoryEntryStatus, CalibrationBand, EvidenceType, PayloadFileType,
TargetType, BusinessValue, BudgetProfile). Only PayloadFileType is
implemented here, because payload_inventory.py (Week 0, Section 12) is
the only Week 0 consumer. The remaining ten are intentionally NOT built
yet -- per the Engineering Constitution's strict week ordering, building
them now would be pulling later weeks' contracts forward (BusinessValue
-> Week 3, EvidenceType -> Week 1, TargetType -> Week 3, BudgetProfile ->
Week 3, etc.) before the sections that define their exact members and
usage are actually implemented. They will be added to THIS file (never a
parallel file) as their owning week arrives -- ontology-first, single
source of truth.
"""

from __future__ import annotations

from enum import Enum


class PayloadFileType(str, Enum):
    """Classification for files under data/payloads/ (Section 3.1).

    INJECTABLE_PAYLOAD files are consumed by payload_engine.py for HTTP
    injection. PATTERN_LIBRARY files are consumed directly by their
    scanner for content matching and are never routed through
    payload_engine.py (Section 3.1). The str mixin makes instances
    serialize as their plain string value (consistent with the
    BusinessValue enum's serialization treatment in Section 11.3).
    """

    INJECTABLE_PAYLOAD = "injectable_payload"
    PATTERN_LIBRARY = "pattern_library"
