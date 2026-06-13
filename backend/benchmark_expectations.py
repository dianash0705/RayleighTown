"""
Evaluate real-log benchmarks against human-labelled expectations.

Configure ``benchmark_expectations.yaml`` (copy from ``benchmark_expectations.yaml.example``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmark_runner import BenchmarkLogEntry, extract_event_records, run_benchmark_entry
from brain import build_alerts_from_sorted_timestamps_ms
from config import BENCHMARK_EXPECTATIONS_PATH


@dataclass(frozen=True)
class BenchmarkExpectation:
    label: str
    native_event_id: int | None = None
    period_ms: float | None = None
    period_tolerance_pct: float = 5.0
    min_confidence: int | None = None
    max_confidence: int | None = None
    min_windows: int | None = None
    must_detect: bool = True
    notes: str = ""


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    path: Path
    log_id: int
    endpoint_id: str
    enabled: bool
    description: str
    expectations: tuple[BenchmarkExpectation, ...]


@dataclass(frozen=True)
class AlertObservation:
    native_event_id: int
    period_ms: float
    confidence: int
    window_count: int
    ts_begin: int
    ts_end: int


@dataclass(frozen=True)
class ExpectationVerdict:
    case_name: str
    expectation: BenchmarkExpectation
    status: str
    message: str
    best_match: AlertObservation | None = None


def _parse_expectation(raw: dict[str, Any]) -> BenchmarkExpectation:
    return BenchmarkExpectation(
        label=str(raw.get("label", "unnamed expectation")),
        native_event_id=_optional_int(raw.get("native_event_id")),
        period_ms=_optional_float(raw.get("period_ms")),
        period_tolerance_pct=float(raw.get("period_tolerance_pct", 5.0)),
        min_confidence=_optional_int(raw.get("min_confidence")),
        max_confidence=_optional_int(raw.get("max_confidence")),
        min_windows=_optional_int(raw.get("min_windows")),
        must_detect=bool(raw.get("must_detect", True)),
        notes=str(raw.get("notes", "")),
    )


def _optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def load_benchmark_cases(config_path: Path | None = None) -> list[BenchmarkCase]:
    path = config_path or BENCHMARK_EXPECTATIONS_PATH
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    cases: list[BenchmarkCase] = []
    for raw_case in payload.get("benchmarks", []):
        file_path = raw_case.get("path")
        if not file_path:
            continue
        expectations = tuple(
            _parse_expectation(item)
            for item in raw_case.get("expectations", [])
        )
        cases.append(
            BenchmarkCase(
                name=str(raw_case.get("name", "unnamed")),
                path=Path(file_path),
                log_id=int(raw_case.get("log_id", 0)),
                endpoint_id=str(raw_case.get("endpoint_id", "benchmark-endpoint")),
                enabled=bool(raw_case.get("enabled", False)),
                description=str(raw_case.get("description", "")),
                expectations=expectations,
            )
        )
    return cases


def _enabled_cases(config_path: Path | None = None) -> list[BenchmarkCase]:
    return [
        case
        for case in load_benchmark_cases(config_path)
        if case.enabled and case.path.exists()
    ]


def _period_matches(candidate_ms: float, expected_ms: float, tolerance_pct: float) -> bool:
    tolerance = expected_ms * (tolerance_pct / 100.0)
    return abs(candidate_ms - expected_ms) <= tolerance


def _collect_alerts(case: BenchmarkCase) -> list[AlertObservation]:
    events = extract_event_records(
        BenchmarkLogEntry(
            name=case.name,
            path=case.path,
            log_id=case.log_id,
            endpoint_id=case.endpoint_id,
            enabled=True,
            description=case.description,
        )
    )
    grouped: dict[int, list[int]] = {}
    for event in sorted(events, key=lambda item: item.timestamp_ms):
        grouped.setdefault(event.native_event_id, []).append(event.timestamp_ms)

    observations: list[AlertObservation] = []
    for native_event_id, timestamps in grouped.items():
        alerts = build_alerts_from_sorted_timestamps_ms(
            timestamps,
            endpoint_id=case.endpoint_id,
            native_event_id=native_event_id,
            method="fourier",
            plot=False,
            show_progress=False,
        )
        if not alerts:
            continue
        for alert in alerts:
            window_count = 0
            breakdown = alert.confidence_breakdown
            if breakdown and breakdown.group:
                window_count = breakdown.group.window_count
            observations.append(
                AlertObservation(
                    native_event_id=native_event_id,
                    period_ms=float(alert.period_ts),
                    confidence=int(alert.confidence),
                    window_count=window_count,
                    ts_begin=int(alert.ts_begin),
                    ts_end=int(alert.ts_end),
                )
            )
    return observations


def _best_match_for_expectation(
    expectation: BenchmarkExpectation,
    alerts: list[AlertObservation],
) -> AlertObservation | None:
    candidates = alerts
    if expectation.native_event_id is not None:
        candidates = [
            alert
            for alert in candidates
            if alert.native_event_id == expectation.native_event_id
        ]
    if expectation.period_ms is not None:
        candidates = [
            alert
            for alert in candidates
            if _period_matches(
                alert.period_ms,
                expectation.period_ms,
                expectation.period_tolerance_pct,
            )
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda alert: alert.confidence)


def evaluate_expectation(
    case_name: str,
    expectation: BenchmarkExpectation,
    alerts: list[AlertObservation],
) -> ExpectationVerdict:
    best_match = _best_match_for_expectation(expectation, alerts)

    if expectation.must_detect:
        if best_match is None:
            return ExpectationVerdict(
                case_name=case_name,
                expectation=expectation,
                status="MISSING",
                message="Expected alert not found.",
            )

        failures: list[str] = []
        if expectation.min_confidence is not None and best_match.confidence < expectation.min_confidence:
            failures.append(
                f"confidence {best_match.confidence} < min {expectation.min_confidence}"
            )
        if expectation.max_confidence is not None and best_match.confidence > expectation.max_confidence:
            failures.append(
                f"confidence {best_match.confidence} > max {expectation.max_confidence}"
            )
        if expectation.min_windows is not None and best_match.window_count < expectation.min_windows:
            failures.append(
                f"windows {best_match.window_count} < min {expectation.min_windows}"
            )

        if failures:
            return ExpectationVerdict(
                case_name=case_name,
                expectation=expectation,
                status="WEAK",
                message="; ".join(failures),
                best_match=best_match,
            )

        return ExpectationVerdict(
            case_name=case_name,
            expectation=expectation,
            status="PASS",
            message=(
                f"Detected period={best_match.period_ms:.0f}ms "
                f"conf={best_match.confidence} windows={best_match.window_count}"
            ),
            best_match=best_match,
        )

    if best_match is None:
        return ExpectationVerdict(
            case_name=case_name,
            expectation=expectation,
            status="PASS",
            message="No matching alert (correctly absent).",
        )

    if expectation.max_confidence is not None and best_match.confidence <= expectation.max_confidence:
        return ExpectationVerdict(
            case_name=case_name,
            expectation=expectation,
            status="PASS",
            message=(
                f"Ghost/low alert present but acceptable: "
                f"period={best_match.period_ms:.0f}ms conf={best_match.confidence}"
            ),
            best_match=best_match,
        )

    return ExpectationVerdict(
        case_name=case_name,
        expectation=expectation,
        status="FALSE_POSITIVE",
        message=(
            f"Unexpected alert too strong: period={best_match.period_ms:.0f}ms "
            f"conf={best_match.confidence}"
        ),
        best_match=best_match,
    )


def analyze_benchmark_case(case: BenchmarkCase) -> list[ExpectationVerdict]:
    alerts = _collect_alerts(case)
    return [
        evaluate_expectation(case.name, expectation, alerts)
        for expectation in case.expectations
    ]


def analyze_configured_benchmarks(config_path: Path | None = None) -> list[ExpectationVerdict]:
    verdicts: list[ExpectationVerdict] = []
    for case in _enabled_cases(config_path):
        verdicts.extend(analyze_benchmark_case(case))
    return verdicts


def format_analysis_report(verdicts: list[ExpectationVerdict]) -> str:
    if not verdicts:
        return "No benchmark expectations evaluated."

    lines: list[str] = []
    current_case = None
    for verdict in verdicts:
        if verdict.case_name != current_case:
            current_case = verdict.case_name
            lines.append("")
            lines.append(f"## {current_case}")

        lines.append(
            f"- [{verdict.status}] {verdict.expectation.label}: {verdict.message}"
        )
        if verdict.expectation.notes:
            lines.append(f"  notes: {verdict.expectation.notes}")

    counts = {
        status: sum(1 for verdict in verdicts if verdict.status == status)
        for status in ("PASS", "MISSING", "WEAK", "FALSE_POSITIVE")
    }
    lines.append("")
    lines.append(
        "Summary: "
        f"pass={counts.get('PASS', 0)} "
        f"missing={counts.get('MISSING', 0)} "
        f"weak={counts.get('WEAK', 0)} "
        f"false_positive={counts.get('FALSE_POSITIVE', 0)}"
    )
    return "\n".join(lines).strip()


def main() -> None:
    verdicts = analyze_configured_benchmarks()
    if not verdicts:
        print(f"No enabled benchmark cases found in {BENCHMARK_EXPECTATIONS_PATH}")
        print("Copy benchmark_expectations.yaml.example and set enabled: true.")
        return
    print(format_analysis_report(verdicts))


if __name__ == "__main__":
    main()
