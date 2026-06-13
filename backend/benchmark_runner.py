"""
Run confidence / detection benchmarks against real log files.

Configure paths in ``benchmark_logs.json`` (copy from ``benchmark_logs.json.example``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brain import EventRecord, build_alerts_from_sorted_timestamps_ms
from config import BENCHMARK_LOGS_CONFIG_PATH
from log_registry import LOG_TYPE_CONFIG


@dataclass(frozen=True)
class BenchmarkLogEntry:
    name: str
    path: Path
    log_id: int
    endpoint_id: str
    enabled: bool = True
    description: str = ""


@dataclass(frozen=True)
class BenchmarkAlertSummary:
    native_event_id: int
    period_ms: float
    confidence: int
    ts_begin: int
    ts_end: int
    window_count: int
    mean_phase_similarity: float | None
    median_base_peak: float | None


@dataclass(frozen=True)
class BenchmarkRunResult:
    entry_name: str
    endpoint_id: str
    event_count: int
    alert_count: int
    top_alerts: tuple[BenchmarkAlertSummary, ...]


def load_benchmark_entries(config_path: Path | None = None) -> list[BenchmarkLogEntry]:
    path = config_path or BENCHMARK_LOGS_CONFIG_PATH
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    entries: list[BenchmarkLogEntry] = []
    for raw_entry in payload.get("logs", []):
        file_path = raw_entry.get("path")
        if not file_path:
            continue
        entries.append(
            BenchmarkLogEntry(
                name=str(raw_entry.get("name", "unnamed")),
                path=Path(file_path),
                log_id=int(raw_entry.get("log_id", 0)),
                endpoint_id=str(raw_entry.get("endpoint_id", "benchmark-endpoint")),
                enabled=bool(raw_entry.get("enabled", False)),
                description=str(raw_entry.get("description", "")),
            )
        )
    return entries


def _enabled_entries(config_path: Path | None = None) -> list[BenchmarkLogEntry]:
    return [
        entry
        for entry in load_benchmark_entries(config_path)
        if entry.enabled and entry.path.exists()
    ]


def extract_event_records(entry: BenchmarkLogEntry) -> list[EventRecord]:
    log_config = LOG_TYPE_CONFIG.get(entry.log_id, LOG_TYPE_CONFIG[999])
    extractor = log_config["extractor"]
    whitelist = log_config.get("event_id_whitelist")

    ingested_events = extractor(entry.path, whitelist, log_id=entry.log_id)
    event_records: list[EventRecord] = []
    for index, event in enumerate(ingested_events):
        event_records.append(
            EventRecord(
                internal_event_id=index + 1,
                native_event_id=int(event["native_event_id"]),
                timestamp_ms=int(event["timestamp_ms"]),
            )
        )
    return event_records


def _alert_summary(alert) -> BenchmarkAlertSummary:
    window_count = 0
    mean_phase_similarity = None
    median_base_peak = None
    breakdown = alert.confidence_breakdown
    if breakdown and breakdown.group:
        group = breakdown.group
        window_count = group.window_count
        mean_phase_similarity = group.mean_phase_similarity
        median_base_peak = group.median_base_peak
    elif breakdown and breakdown.window:
        median_base_peak = breakdown.window.base_peak

    return BenchmarkAlertSummary(
        native_event_id=0,
        period_ms=float(alert.period_ts),
        confidence=int(alert.confidence),
        ts_begin=int(alert.ts_begin),
        ts_end=int(alert.ts_end),
        window_count=window_count,
        mean_phase_similarity=mean_phase_similarity,
        median_base_peak=median_base_peak,
    )


def _group_events_by_native_id(events: list[EventRecord]) -> dict[int, list[int]]:
    grouped: dict[int, list[int]] = {}
    for event in sorted(events, key=lambda item: item.timestamp_ms):
        grouped.setdefault(event.native_event_id, []).append(event.timestamp_ms)
    return grouped


def run_benchmark_entry(entry: BenchmarkLogEntry) -> BenchmarkRunResult:
    events = extract_event_records(entry)
    alert_cores: list[tuple[int, Any]] = []
    for native_event_id, timestamps in _group_events_by_native_id(events).items():
        cores = build_alerts_from_sorted_timestamps_ms(
            timestamps,
            endpoint_id=entry.endpoint_id,
            native_event_id=native_event_id,
            method="fourier",
            plot=False,
            show_progress=False,
        )
        if cores:
            alert_cores.extend((native_event_id, core) for core in cores)

    summaries: list[BenchmarkAlertSummary] = []
    for native_event_id, alert in sorted(alert_cores, key=lambda item: item[1].confidence, reverse=True)[:10]:
        summary = _alert_summary(alert)
        summaries.append(
            BenchmarkAlertSummary(
                native_event_id=native_event_id,
                period_ms=summary.period_ms,
                confidence=summary.confidence,
                ts_begin=summary.ts_begin,
                ts_end=summary.ts_end,
                window_count=summary.window_count,
                mean_phase_similarity=summary.mean_phase_similarity,
                median_base_peak=summary.median_base_peak,
            )
        )

    return BenchmarkRunResult(
        entry_name=entry.name,
        endpoint_id=entry.endpoint_id,
        event_count=len(events),
        alert_count=len(alert_cores),
        top_alerts=tuple(summaries),
    )


def run_configured_benchmarks(config_path: Path | None = None) -> list[BenchmarkRunResult]:
    return [run_benchmark_entry(entry) for entry in _enabled_entries(config_path)]


def format_benchmark_report(results: list[BenchmarkRunResult]) -> str:
    lines: list[str] = []
    for result in results:
        lines.append(
            f"{result.entry_name}: events={result.event_count} alerts={result.alert_count}"
        )
        for alert in result.top_alerts[:5]:
            lines.append(
                "  "
                f"event={alert.native_event_id} period={alert.period_ms:.0f}ms "
                f"conf={alert.confidence} windows={alert.window_count} "
                f"phase_sim={alert.mean_phase_similarity} base={alert.median_base_peak}"
            )
    return "\n".join(lines)


def main() -> None:
    results = run_configured_benchmarks()
    if not results:
        print(f"No enabled benchmark logs found in {BENCHMARK_LOGS_CONFIG_PATH}")
        return
    print(format_benchmark_report(results))


if __name__ == "__main__":
    main()
