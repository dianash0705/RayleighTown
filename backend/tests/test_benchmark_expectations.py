"""Optional labelled real-log benchmarks from benchmark_expectations.yaml."""

from pathlib import Path

import pytest

from benchmark_expectations import (
    analyze_configured_benchmarks,
    format_analysis_report,
    load_benchmark_cases,
)
from config import BENCHMARK_EXPECTATIONS_PATH


@pytest.mark.slow
class TestBenchmarkExpectations:
    def test_example_yaml_loads(self):
        example_path = BENCHMARK_EXPECTATIONS_PATH.parent / "benchmark_expectations.yaml.example"
        cases = load_benchmark_cases(example_path)
        assert len(cases) == 2
        assert cases[0].expectations

    def test_run_enabled_expectations_when_configured(self):
        user_config = Path(BENCHMARK_EXPECTATIONS_PATH)
        if not user_config.exists():
            pytest.skip("Create backend/benchmark_expectations.yaml from the example file.")

        enabled = [case for case in load_benchmark_cases(user_config) if case.enabled]
        if not enabled:
            pytest.skip("No enabled entries in benchmark_expectations.yaml")

        missing = [case.name for case in enabled if not case.path.exists()]
        if missing:
            pytest.skip(f"Configured log paths missing: {', '.join(missing)}")

        verdicts = analyze_configured_benchmarks(user_config)
        report = format_analysis_report(verdicts)
        assert "Summary:" in report

        failures = [
            verdict
            for verdict in verdicts
            if verdict.status in {"MISSING", "FALSE_POSITIVE"}
        ]
        if failures:
            lines = "\n".join(
                f"{verdict.expectation.label}: {verdict.message}" for verdict in failures
            )
            pytest.fail("Benchmark expectation failures:\n" + lines)
