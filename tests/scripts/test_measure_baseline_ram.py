"""
Implements: Section 3 test coverage -- measure_baseline_ram.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import os

import pytest

from scripts.measure_baseline_ram import (
    SCANNER_HARD_MB,
    SCANNER_WARN_MB,
    TOTAL_AT_CONCURRENCY_5_CAP_MB,
    BaselineRamReport,
    ConcurrencyAction,
    ScannerRamMeasurement,
    classify,
    measure_process_rss_mb,
    measure_scanner_registry,
)


class TestClassify:
    def test_at_or_below_200mb_is_ok(self):
        assert classify(0.0) == ConcurrencyAction.OK
        assert classify(SCANNER_WARN_MB) == ConcurrencyAction.OK

    def test_above_200mb_reduces_to_3(self):
        assert classify(SCANNER_WARN_MB + 0.01) == ConcurrencyAction.REDUCE_TO_3
        assert classify(SCANNER_HARD_MB) == ConcurrencyAction.REDUCE_TO_3

    def test_above_400mb_reduces_to_1(self):
        assert classify(SCANNER_HARD_MB + 0.01) == ConcurrencyAction.REDUCE_TO_1
        assert classify(10_000.0) == ConcurrencyAction.REDUCE_TO_1


class TestMeasureProcessRssMb:
    def test_measures_current_process_as_positive_number(self):
        rss = measure_process_rss_mb(os.getpid())
        assert rss > 0.0

    def test_defaults_to_current_process(self):
        assert measure_process_rss_mb() > 0.0

    def test_nonexistent_pid_raises(self):
        import psutil

        with pytest.raises(psutil.NoSuchProcess):
            measure_process_rss_mb(pid=2**30)


class TestMeasureScannerRegistry:
    def test_empty_or_none_registry_returns_empty_list(self):
        assert measure_scanner_registry(None) == []
        assert measure_scanner_registry({}) == []

    def test_nonempty_registry_raises_not_implemented(self):
        # Week 0: SCANNER_REGISTRY doesn't exist until Week 5 and scanners
        # don't exist until Week 7 -- this must fail loudly, not silently
        # fabricate a measurement.
        with pytest.raises(NotImplementedError):
            measure_scanner_registry({"xss_scanner": object()})


class TestBaselineRamReport:
    def test_total_at_concurrency_5_uses_top_5_only(self):
        measurements = [
            ScannerRamMeasurement(f"scanner_{i}", rss_mb=100.0 * i, action=classify(100.0 * i))
            for i in range(1, 8)  # 100..700 MB
        ]
        report = BaselineRamReport(scanner_measurements=measurements)
        # top 5 of [100,200,300,400,500,600,700] = 300+400+500+600+700 = 2500
        assert report.total_at_concurrency_5_mb == 2500.0

    def test_preflight_failure_false_with_fewer_than_5_scanners(self):
        measurements = [
            ScannerRamMeasurement("scanner_1", rss_mb=10_000.0, action=ConcurrencyAction.REDUCE_TO_1)
        ]
        report = BaselineRamReport(scanner_measurements=measurements)
        assert report.preflight_failure is False  # not enough data to evaluate the gate

    def test_preflight_failure_true_when_top5_exceeds_cap(self):
        measurements = [
            ScannerRamMeasurement(f"scanner_{i}", rss_mb=300.0, action=classify(300.0)) for i in range(5)
        ]  # 5 x 300MB = 1500MB > 1024MB cap
        report = BaselineRamReport(scanner_measurements=measurements)
        assert report.total_at_concurrency_5_mb == 1500.0
        assert report.total_at_concurrency_5_mb > TOTAL_AT_CONCURRENCY_5_CAP_MB
        assert report.preflight_failure is True

    def test_preflight_failure_false_when_top5_within_cap(self):
        measurements = [
            ScannerRamMeasurement(f"scanner_{i}", rss_mb=100.0, action=classify(100.0)) for i in range(5)
        ]  # 5 x 100MB = 500MB < 1024MB cap
        report = BaselineRamReport(scanner_measurements=measurements)
        assert report.preflight_failure is False
