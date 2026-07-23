"""
Implements: Section 3 -- core/verifier/evidence_chain.py
  ("7 core + 7 substitutes; 1 unique sub = 1 slot")
Also implements: Section 5.4 (vuln_thresholds.yaml, complete), Section 5.2
(evidence substitutes + counting rule).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 1 SCOPE: this module loads configs/vuln_thresholds.yaml and builds
correctly-configured EvidenceChain instances (core/ontology/findings.py)
from it -- resolving the right `min_required` per vuln_type, and
resolving which named substitute stands in for a given unavailable core
type. It does NOT collect real evidence from live scanner traffic --
that requires the 29 scanners to exist (Week 7). The counting RULE
itself (`len(set(collected_types)) >= min_required`) lives on
EvidenceChain as a property (core/ontology/findings.py), since it is
pure arithmetic with no config dependency; this module owns everything
that DOES need the YAML.

`min_signals` (Fast Lane's signal-gate threshold, Section 5.4) is parsed
and validated here too, since it lives in the same YAML file as one
coherent block (Section 5.4 shows both together) -- same precedent as
Week 0's scope.yaml (docs/DECISIONS.md item 5): loading the whole config
file now isn't "building ahead" since it's data, not executable
behavior. It is not yet CONSUMED by any Week 1 logic (Fast Lane doesn't
exist until Week 5) -- exposed on VulnThresholds for whenever it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.ontology.enums import EvidenceType
from core.ontology.findings import EvidenceChain, TriageResult

_DEFAULT_CONFIG_PATH = Path("configs/vuln_thresholds.yaml")

_REQUIRED_TOP_LEVEL_KEYS = ("min_signals", "min_evidence_types", "evidence_substitutes")


class VulnThresholdsConfigError(Exception):
    """Raised when vuln_thresholds.yaml is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class VulnThresholds:
    """Parsed, validated configs/vuln_thresholds.yaml (Section 5.4).

    Attributes:
        min_signals: vuln_type -> minimum Fast Lane signal count. Not yet
            consumed (Fast Lane is Week 5); parsed and validated now
            because it lives in the same YAML file.
        min_evidence_types: vuln_type -> minimum unique EvidenceType
            count required for EvidenceChain.meets_per_vuln_minimum.
        evidence_substitutes: vuln_type -> {unavailable_core_value:
            substitute_value}, both as the plain string values (not
            EvidenceType instances) as they appear in the YAML.
    """

    min_signals: dict[str, int]
    min_evidence_types: dict[str, int]
    evidence_substitutes: dict[str, dict[str, str]]

    def min_required_for(self, vuln_type: str) -> int:
        """Section 5.4: `min_evidence_types[vuln_type]`, falling back to `default`.

        Args:
            vuln_type: e.g. "sqli". Any value not present as a key falls
                back to the YAML's own `default` entry, matching
                min_evidence_types' documented fallback (Section 5.4:
                every table ends with a `default: 2` row).

        Returns:
            The minimum unique EvidenceType count required.
        """
        return self.min_evidence_types.get(vuln_type, self.min_evidence_types["default"])

    def min_signals_for(self, vuln_type: str) -> int:
        """Section 5.4: `min_signals[vuln_type]`, falling back to `default`."""
        return self.min_signals.get(vuln_type, self.min_signals["default"])

    def resolve_substitute(self, vuln_type: str, unavailable_core: EvidenceType) -> EvidenceType | None:
        """Section 5.2: maps an unavailable core type to its named substitute.

        Args:
            vuln_type: e.g. "sqli".
            unavailable_core: the core EvidenceType that could not be
                collected for this finding (e.g. EvidenceType.OOB_INTERACTION).

        Returns:
            The substitute EvidenceType Section 5.2 defines for this
            (vuln_type, unavailable_core) pair, or None if this
            vuln_type has no substitute mapping at all, or none for
            this specific core type. Section 5.2 only defines
            substitutes for 5 of the 29 vuln types (xss, sqli, csrf,
            business_logic, idor/bac share cross_account_readback,
            race) -- None is the correct, expected answer for the other
            24 (Section 5.3's matrix marks their unachievable core
            types as unconditionally unavailable with no substitute,
            which is why their `min_evidence_types` never exceeds their
            achievable core-type count).
        """
        vuln_map = self.evidence_substitutes.get(vuln_type)
        if not vuln_map:
            return None
        raw = vuln_map.get(unavailable_core.value)
        return EvidenceType(raw) if raw is not None else None


def load_vuln_thresholds(config_path: Path = _DEFAULT_CONFIG_PATH) -> VulnThresholds:
    """Loads and validates configs/vuln_thresholds.yaml.

    Args:
        config_path: Path to vuln_thresholds.yaml.

    Returns:
        A VulnThresholds instance.

    Raises:
        VulnThresholdsConfigError: If the file is missing, is not valid
            YAML, is not a mapping, is missing one of the three required
            top-level keys, or either `min_signals`/`min_evidence_types`
            lacks the `default` fallback entry Section 5.4 requires.
    """
    if not config_path.is_file():
        raise VulnThresholdsConfigError(f"vuln_thresholds.yaml not found at {config_path}")

    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise VulnThresholdsConfigError(f"vuln_thresholds.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise VulnThresholdsConfigError("vuln_thresholds.yaml must be a mapping at the top level")

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in raw:
            raise VulnThresholdsConfigError(f"vuln_thresholds.yaml missing required key: {key!r}")

    min_signals = raw["min_signals"]
    min_evidence_types = raw["min_evidence_types"]
    evidence_substitutes = raw["evidence_substitutes"]

    if not isinstance(min_signals, dict) or "default" not in min_signals:
        raise VulnThresholdsConfigError("min_signals missing required 'default' fallback (Section 5.4)")
    if not isinstance(min_evidence_types, dict) or "default" not in min_evidence_types:
        raise VulnThresholdsConfigError("min_evidence_types missing required 'default' fallback (Section 5.4)")
    if not isinstance(evidence_substitutes, dict):
        raise VulnThresholdsConfigError("evidence_substitutes must be a mapping (Section 5.4)")

    return VulnThresholds(
        min_signals=dict(min_signals),
        min_evidence_types=dict(min_evidence_types),
        evidence_substitutes={k: dict(v) for k, v in evidence_substitutes.items()},
    )


def build_evidence_chain(
    vuln_type: str,
    collected_types: list[EvidenceType],
    triage_l1: TriageResult | None = None,
    triage_l2: TriageResult | None = None,
    triage_l3: TriageResult | None = None,
    thresholds: VulnThresholds | None = None,
) -> EvidenceChain:
    """Builds an EvidenceChain with `min_required` resolved from vuln_thresholds.yaml.

    This is the intended construction path for real EvidenceChain
    instances -- it ensures `min_required` reflects Section 5.4 rather
    than EvidenceChain's own generic dataclass default (which exists
    only so the dataclass fails closed to a real, YAML-consistent number
    if ever constructed directly without going through this factory --
    see core/ontology/findings.py's EvidenceChain docstring).

    Args:
        vuln_type: e.g. "sqli".
        collected_types: EvidenceType values collected so far.
        triage_l1: AutonomousTriage L1 outcome, if known yet.
        triage_l2: AutonomousTriage L2 outcome, if known yet.
        triage_l3: AutonomousTriage L3 outcome, if known yet.
        thresholds: Pre-loaded VulnThresholds. Loads from the default
            config path if not supplied -- callers building many chains
            in one process should load once and pass it in rather than
            re-reading the YAML per call.

    Returns:
        A populated EvidenceChain.

    Raises:
        VulnThresholdsConfigError: Propagated from load_vuln_thresholds
            if `thresholds` was not supplied and the default config
            cannot be loaded.
    """
    cfg = thresholds if thresholds is not None else load_vuln_thresholds()
    return EvidenceChain(
        vuln_type=vuln_type,
        collected_types=list(collected_types),
        min_required=cfg.min_required_for(vuln_type),
        triage_l1=triage_l1,
        triage_l2=triage_l2,
        triage_l3=triage_l3,
    )
