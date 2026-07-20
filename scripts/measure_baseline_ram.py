#!/usr/bin/env python3
"""
Implements: Section 3 -- scripts/measure_baseline_ram.py ("psutil
snapshot; HARD GATE if any scanner > 200 MB")
Blueprint: bb_agent_v6.6_final_blueprint.md

Section 9.1 hard gate: scanner > 200 MB -> reduce concurrency to 3.
Scanner > 400 MB -> reduce concurrency to 1. Total scanner RAM > 1 GB at
concurrency=5 -> preflight failure, agent startup blocked.

WEEK 0 SCOPE NOTE: The 29 scanners do not exist until Week 7 and
SCANNER_REGISTRY does not exist until Week 5 (Section 12), so this
script's scanner-measurement path has nothing to measure yet. This is
exactly why the "scanner_ram_gate" preflight check is one of the two
Week-0 checks that report NOT_YET_IMPLEMENTED (agreed resolution, item
3) rather than PASS or FAIL. What Week 0 CAN verify for real is the
measurement mechanism itself (measure_process_rss_mb) against a live
process -- see --self-check.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from enum import Enum

import psutil

BYTES_PER_MB = 1024 * 1024

# Section 9.1 hard-gate thresholds
SCANNER_WARN_MB = 200.0  # > 200 MB -> reduce concurrency to 3
SCANNER_HARD_MB = 400.0  # > 400 MB -> reduce concurrency to 1
TOTAL_AT_CONCURRENCY_5_CAP_MB = 1024.0  # > 1 GB at concurrency=5 -> preflight failure


class ConcurrencyAction(str, Enum):
    """Section 9.1 hard-gate action for a single scanner's measured RAM."""

    OK = "ok"  # <= 200 MB: no action
    REDUCE_TO_3 = "reduce_concurrency_to_3"  # > 200 MB
    REDUCE_TO_1 = "reduce_concurrency_to_1"  # > 400 MB


@dataclass(frozen=True)
class ScannerRamMeasurement:
    """One scanner's measured RAM under load (Section 9.1).

    Attributes:
        scanner_id: SCANNER_REGISTRY key.
        rss_mb: Resident set size in MB.
        action: The Section 9.1 hard-gate action this measurement triggers.
    """

    scanner_id: str
    rss_mb: float
    action: ConcurrencyAction


@dataclass
class BaselineRamReport:
    """Full Week-0-runnable output of a measure_baseline_ram.py pass.

    Attributes:
        scanner_measurements: Per-scanner results (empty until Week 7).
    """

    scanner_measurements: list[ScannerRamMeasurement] = field(default_factory=list)

    @property
    def total_at_concurrency_5_mb(self) -> float:
        """sum(top 5 scanner rss_mb) -- the quantity Section 9.1's ">1 GB
        at concurrency=5" gate checks."""
        top5 = sorted((m.rss_mb for m in self.scanner_measurements), reverse=True)[:5]
        return sum(top5)

    @property
    def preflight_failure(self) -> bool:
        if len(self.scanner_measurements) < 5:
            return False  # not enough scanners registered to evaluate the gate yet
        return self.total_at_concurrency_5_mb > TOTAL_AT_CONCURRENCY_5_CAP_MB


def classify(rss_mb: float) -> ConcurrencyAction:
    """Applies Section 9.1's hard-gate thresholds to one measurement."""
    if rss_mb > SCANNER_HARD_MB:
        return ConcurrencyAction.REDUCE_TO_1
    if rss_mb > SCANNER_WARN_MB:
        return ConcurrencyAction.REDUCE_TO_3
    return ConcurrencyAction.OK


def measure_process_rss_mb(pid: int | None = None) -> float:
    """Measures one process's resident set size in MB via psutil.

    Args:
        pid: Process ID to measure. Defaults to the current process.

    Returns:
        RSS in MB.

    Raises:
        psutil.NoSuchProcess: If pid does not exist.
    """
    proc = psutil.Process(pid)
    return proc.memory_info().rss / BYTES_PER_MB


def measure_scanner_registry(
    scanner_registry: dict[str, object] | None,
) -> list[ScannerRamMeasurement]:
    """Measures every registered scanner's RSS under load.

    Args:
        scanner_registry: SCANNER_REGISTRY (Week 5+). None or empty means
            no scanners are registered yet -- returns an empty list rather
            than raising, since that is the expected Week 0-6 state.

    Returns:
        One ScannerRamMeasurement per registered scanner.

    Raises:
        NotImplementedError: If scanner_registry is non-empty. Real
            per-scanner measurement requires spinning up each scanner
            under representative load, which is not implementable before
            the 29 scanner implementations exist (Week 7) -- this is an
            explicit extension point, not a silent stub that pretends to
            measure something it can't.
    """
    if not scanner_registry:
        return []
    raise NotImplementedError(
        "Scanner RAM measurement requires SCANNER_REGISTRY (Week 5) and "
        "the 29 scanner implementations (Week 7); not available yet."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Measure this script's own process as a smoke test of the "
        "measurement mechanism, since no scanners exist yet (Week 0).",
    )
    args = parser.parse_args()

    if args.self_check:
        rss = measure_process_rss_mb()
        action = classify(rss)
        print(f"[RAM_SELF_CHECK] current process RSS = {rss:.2f} MB, action={action.value}")

    report = BaselineRamReport(scanner_measurements=measure_scanner_registry(None))
    print(
        "[SCANNER_RAM_GATE] NOT_YET_IMPLEMENTED -- SCANNER_REGISTRY does not exist "
        "until Week 5, and no scanners exist until Week 7 (Section 12). This check "
        "will measure for real once both land."
    )
    print(f"[SCANNER_RAM_GATE] measurements so far: {len(report.scanner_measurements)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
