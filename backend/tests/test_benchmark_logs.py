"""Optional real-log benchmarks configured in benchmark_logs.json."""

from pathlib import Path

import pytest

from benchmark_runner import (
    format_benchmark_report,
    load_benchmark_entries,
    run_benchmark_entry,
    run_configured_benchmarks,
)
from config import BENCHMARK_LOGS_CONFIG_PATH


@pytest.mark.slow
class TestConfiguredBenchmarkLogs:
    def test_example_config_loads(self):
        example_path = BENCHMARK_LOGS_CONFIG_PATH.parent / "benchmark_logs.json.example"
        entries = load_benchmark_entries(example_path)
        assert len(entries) == 2
        assert entries[0].name == "clean_windows_security"

    def test_run_enabled_real_logs_when_configured(self, tmp_path):
        user_config = Path(BENCHMARK_LOGS_CONFIG_PATH)
        if not user_config.exists():
            pytest.skip("Create backend/benchmark_logs.json from benchmark_logs.json.example")

        entries = [entry for entry in load_benchmark_entries(user_config) if entry.enabled]
        if not entries:
            pytest.skip("No enabled entries in benchmark_logs.json")

        missing = [entry.name for entry in entries if not entry.path.exists()]
        if missing:
            pytest.skip(f"Configured log paths missing: {', '.join(missing)}")

        results = run_configured_benchmarks(user_config)
        assert results
        report = format_benchmark_report(results)
        assert "events=" in report
        assert "alerts=" in report

    def test_single_entry_runs_without_error(self):
        user_config = Path(BENCHMARK_LOGS_CONFIG_PATH)
        if not user_config.exists():
            pytest.skip("Create backend/benchmark_logs.json from benchmark_logs.json.example")

        entries = [entry for entry in load_benchmark_entries(user_config) if entry.enabled]
        if not entries or not entries[0].path.exists():
            pytest.skip("No runnable enabled benchmark log configured")

        result = run_benchmark_entry(entries[0])
        assert result.event_count >= 0
        assert result.alert_count >= 0
