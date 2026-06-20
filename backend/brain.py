import logging
import math
from dataclasses import dataclass
from typing import Callable, Iterable, List
from bisect import bisect_left, bisect_right

from tqdm.auto import tqdm

from fourier import (
    filter_top_percent,
    finding_max,
    fourier_transform,
    local_max_suppression,
    plot_fourier_points,
    filter_by_snr,
    get_median_value,
    filter_by_harmony,
    get_candidate_period_groups_ms,
    resolve_superharmonic_canonical_period,
    resolve_subharmonic_alias_period,
    period_supported_by_event_spacing,
    rerank_harmonic_peaks_by_spacing,
    compute_spacing_coherence,
    compute_period_median_gap_fit,
    compute_spacing_selection_score,
)
from config import CONFIDENCE_SCORING_CONFIG, HARMONIC_ANALYSIS_CONFIG
from confidence_log import log_confidence_event
from event_matching import MatchedEvent, match_events_to_alert
from confidence_scoring import (
    ConfidenceBreakdown,
    WindowSnapshot,
    apply_evidence_penalty,
    build_window_snapshot,
    compute_evidence_sufficiency_penalty,
    compute_group_confidence,
    compute_window_confidence,
    finalize_uncorroborated_window_confidence,
    should_publish_alert,
)

logger = logging.getLogger(__name__)

UNKNOWN_TIMESTAMP_MS = -1
UNKNOWN_CONFIDENCE = 0

@dataclass(frozen=True)
class EventRecord:
    internal_event_id: int
    native_event_id: int
    timestamp_ms: int


@dataclass(frozen=True)
class AlertRecord:
    endpoint_id: str
    native_event_id: int
    matched_events: tuple[MatchedEvent, ...]
    ts_begin: int = UNKNOWN_TIMESTAMP_MS
    ts_end: int = UNKNOWN_TIMESTAMP_MS
    period_ts: float = math.nan
    confidence: int = UNKNOWN_CONFIDENCE
    phase: float = math.nan

    @property
    def event_ids(self) -> list[int]:
        return [matched_event.internal_event_id for matched_event in self.matched_events]


@dataclass(frozen=True)
class AlertCore:
    ts_begin: int = UNKNOWN_TIMESTAMP_MS
    ts_end: int = UNKNOWN_TIMESTAMP_MS
    period_ts: float = math.nan
    confidence: int = UNKNOWN_CONFIDENCE
    phase: float = math.nan
    window_snapshots: tuple[WindowSnapshot, ...] = ()
    confidence_breakdown: ConfidenceBreakdown | None = None


FetchEventsFn = Callable[[str], Iterable[EventRecord]]
PublishAlertsFn = Callable[[str, list[AlertRecord]], int]
AlertBuilderFn = Callable[[list[int], "AlertBuildContext"], AlertCore | None]


@dataclass(frozen=True)
class AlertBuildContext:
    endpoint_id: str
    native_event_id: int
    period_candidates_ms: list[float] | None = None
    plot: bool = False
    show_progress: bool = False


ALERT_BUILDERS: dict[str, AlertBuilderFn] = {}


def register_alert_builder(name: str, builder: AlertBuilderFn) -> None:
    ALERT_BUILDERS[name] = builder


def get_alert_builder(name: str) -> AlertBuilderFn:
    try:
        return ALERT_BUILDERS[name]
    except KeyError as err:
        available = ", ".join(sorted(ALERT_BUILDERS)) or "none"
        raise ValueError(f"Unknown alert builder '{name}'. Available builders: {available}") from err


def _group_logs_by_native_event(events: Iterable[EventRecord]):
    grouped = {}
    for event in events:
        grouped.setdefault(event.native_event_id, []).append(event)
    return grouped


def _is_harmonic_period(
    base_period_ms: float,
    candidate_period_ms: float,
    tolerance_ratio: float,
) -> bool:
    if candidate_period_ms <= 0:
        return False

    ratio = base_period_ms / candidate_period_ms
    nearest_multiplier = round(ratio)
    if nearest_multiplier < 2:
        return False

    return abs(ratio - nearest_multiplier) <= tolerance_ratio


def suppress_harmonic_ghost_alerts(alerts: list[AlertCore]) -> list[AlertCore]:
    config = HARMONIC_ANALYSIS_CONFIG
    if not config.ghost_suppression_enabled or not alerts:
        return alerts

    filtered_alerts: list[AlertCore] = []
    suppression_sources: list[AlertCore] = []
    ordered_alerts = sorted(alerts, key=lambda alert_core: alert_core.period_ts, reverse=True)

    for alert_core in ordered_alerts:
        suppress_alert = False

        for source_alert_core in suppression_sources:
            if not _is_harmonic_period(
                source_alert_core.period_ts,
                alert_core.period_ts,
                config.harmonic_tolerance_ratio,
            ):
                continue

            if (not config.phase_ghost_suppression_enabled) or (
                math.cos(alert_core.phase - source_alert_core.phase) >= config.phase_similarity_threshold
            ):
                suppress_alert = True
                break

        suppression_sources.append(alert_core)

        if not suppress_alert:
            filtered_alerts.append(alert_core)

    return filtered_alerts


def _build_window_ranges(start_ms: int, end_ms: int, window_size_ms: int) -> list[tuple[int, int]]:
    if start_ms > end_ms:
        return []

    if start_ms == end_ms or (end_ms - start_ms) <= window_size_ms:
        return [(start_ms, end_ms)]

    step_ms = max(1, int(window_size_ms * (1 - HARMONIC_ANALYSIS_CONFIG.window_overlap_ratio)))
    window_ranges: list[tuple[int, int]] = []

    current_start = start_ms
    while current_start + window_size_ms <= end_ms:
        window_ranges.append((current_start, current_start + window_size_ms))
        current_start += step_ms

    final_start = max(start_ms, end_ms - window_size_ms)
    if not window_ranges or window_ranges[-1][0] != final_start:
        window_ranges.append((final_start, end_ms))

    return window_ranges


def _build_group_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
    window_size_ms: int,
    impact_range: tuple[int, int] | None = None,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < HARMONIC_ANALYSIS_CONFIG.min_events_for_alert:
        return None

    alerts: list[AlertCore] = []
    window_ranges = _build_window_ranges(sorted_timestamps_ms[0], sorted_timestamps_ms[-1], window_size_ms)

    for window_start_ms, window_end_ms in window_ranges:
        if impact_range is not None and not _ranges_overlap(
            window_start_ms,
            window_end_ms,
            impact_range[0],
            impact_range[1],
        ):
            continue

        left_index = bisect_left(sorted_timestamps_ms, window_start_ms)
        right_index = bisect_right(sorted_timestamps_ms, window_end_ms)
        window_timestamps_ms = sorted_timestamps_ms[left_index:right_index]

        if len(window_timestamps_ms) < HARMONIC_ANALYSIS_CONFIG.min_events_for_alert:
            continue

        alert_cores = build_fourier_alert_from_sorted_timestamps_ms(window_timestamps_ms, context)
        if not alert_cores:
            continue

        alerts.extend(alert_cores)

    return alerts or None


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a <= end_b and start_b <= end_a


def _periods_match(period_a: float, period_b: float, tolerance_ratio: float = 0.05) -> bool:
    if math.isnan(period_a) or math.isnan(period_b):
        return False

    scale = max(1.0, abs(period_a), abs(period_b))
    return abs(period_a - period_b) <= (scale * tolerance_ratio)


def _dedupe_window_snapshots(snapshots: list[WindowSnapshot]) -> list[WindowSnapshot]:
    seen_keys: set[tuple[int, int, float]] = set()
    unique_snapshots: list[WindowSnapshot] = []
    for snapshot in snapshots:
        key = (snapshot.ts_begin, snapshot.ts_end, round(snapshot.period_ts, 3))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_snapshots.append(snapshot)
    return unique_snapshots


def _log_group_confidence(
    alert_core: AlertCore,
    *,
    endpoint_id: str | None = None,
    native_event_id: int | None = None,
) -> None:
    if not CONFIDENCE_SCORING_CONFIG.confidence_logging_enabled:
        return
    if alert_core.confidence_breakdown is None:
        return

    log_confidence_event(
        "group_merge",
        {
            "endpoint_id": endpoint_id,
            "native_event_id": native_event_id,
            "period_ms": alert_core.period_ts,
            "ts_begin": alert_core.ts_begin,
            "ts_end": alert_core.ts_end,
            "confidence": alert_core.confidence,
            "breakdown": alert_core.confidence_breakdown.to_dict(),
        },
    )


def _merge_alert_cores(
    left: AlertCore,
    right: AlertCore,
    *,
    endpoint_id: str | None = None,
    native_event_id: int | None = None,
) -> AlertCore:
    merged_snapshots = _dedupe_window_snapshots(
        list(left.window_snapshots) + list(right.window_snapshots)
    )
    group_breakdown = compute_group_confidence(merged_snapshots)
    representative = max(merged_snapshots, key=lambda snapshot: snapshot.window_confidence)

    merged_alert = AlertCore(
        ts_begin=min(left.ts_begin, right.ts_begin),
        ts_end=max(left.ts_end, right.ts_end),
        period_ts=representative.period_ts,
        confidence=group_breakdown.final_confidence,
        phase=representative.phase,
        window_snapshots=tuple(merged_snapshots),
        confidence_breakdown=ConfidenceBreakdown(level="group", group=group_breakdown),
    )
    _log_group_confidence(
        merged_alert,
        endpoint_id=endpoint_id,
        native_event_id=native_event_id,
    )
    return merged_alert


def _merge_overlapping_alert_cores(
    alerts: list[AlertCore],
    *,
    endpoint_id: str | None = None,
    native_event_id: int | None = None,
) -> list[AlertCore]:
    if not alerts:
        return []

    merged_alerts: list[AlertCore] = []
    for alert_core in sorted(alerts, key=lambda alert: (alert.period_ts, alert.ts_begin, alert.ts_end)):
        candidate = alert_core
        index = 0
        while index < len(merged_alerts):
            existing_alert = merged_alerts[index]
            if not _periods_match(existing_alert.period_ts, candidate.period_ts):
                index += 1
                continue

            if not _ranges_overlap(existing_alert.ts_begin, existing_alert.ts_end, candidate.ts_begin, candidate.ts_end):
                index += 1
                continue

            candidate = _merge_alert_cores(
                existing_alert,
                candidate,
                endpoint_id=endpoint_id,
                native_event_id=native_event_id,
            )
            merged_alerts.pop(index)

        merged_alerts.append(candidate)

    return sorted(merged_alerts, key=lambda alert: (alert.ts_begin, alert.ts_end, alert.period_ts))


def _build_windowed_fourier_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
    impact_range: tuple[int, int] | None = None,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < HARMONIC_ANALYSIS_CONFIG.min_events_for_alert:
        return None

    try:
        candidate_groups = get_candidate_period_groups_ms(sorted_timestamps_ms[-1] - sorted_timestamps_ms[0])
    except ValueError:
        return None

    alerts: list[AlertCore] = []
    for group in sorted(candidate_groups, key=lambda candidate_group: candidate_group.window_size_ms, reverse=True):
        grouped_alert_cores = _build_group_alerts_from_sorted_timestamps_ms(
            sorted_timestamps_ms,
            AlertBuildContext(
                endpoint_id=context.endpoint_id,
                native_event_id=context.native_event_id,
                period_candidates_ms=group.periods_ms,
                plot=context.plot,
                show_progress=context.show_progress,
            ),
            window_size_ms=group.window_size_ms,
            impact_range=impact_range,
        )
        if grouped_alert_cores:
            alerts.extend(grouped_alert_cores)

    if not alerts:
        return None

    alerts = suppress_harmonic_ghost_alerts(alerts)

    merged_alerts = _merge_overlapping_alert_cores(
        alerts,
        endpoint_id=context.endpoint_id,
        native_event_id=context.native_event_id,
    )
    return merged_alerts or None


def build_fourier_alert_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < HARMONIC_ANALYSIS_CONFIG.min_events_for_alert:
        return None

    harmonic_config = HARMONIC_ANALYSIS_CONFIG
    period_candidates_ms, magnitudes, phases = fourier_transform(
        sorted_timestamps_ms,
        period_candidates_ms=context.period_candidates_ms,
        show_progress=context.show_progress,
        include_phase=True,
    )
    if not period_candidates_ms or not magnitudes:
        return None

    points = list(zip(period_candidates_ms, magnitudes))
    phase_by_period = {period_ms: phase for period_ms, phase in zip(period_candidates_ms, phases)}

    median = get_median_value(magnitudes)
    distances_from_medians = []
    for magnitude in magnitudes:
        distance_from_median = abs(magnitude - median)
        distances_from_medians.append(distance_from_median)
    mad = get_median_value(distances_from_medians)

    local_max_indices = finding_max(magnitudes)
    local_max_points = [points[index] for index in local_max_indices]
    # if not local_max_points:
    #     return None

    suppressed_local_max_points = local_max_suppression(
        radius=harmonic_config.suppression_radius_ms,
        local_maxs=local_max_points,
    )
    # if not suppressed_local_max_points:
    #     return None

    top_percent_points = filter_top_percent(
        suppressed_local_max_points,
        top_percent=harmonic_config.top_percent,
    )
    # if not top_percent_points:
    #     return None

    top_percent_points = suppressed_local_max_points
    high_snr_points = filter_by_snr(
        top_percent_points,
        median=median,
        mad=mad,
        min_snr=harmonic_config.snr_threshold,
    )

    harmonic_points = filter_by_harmony(
        high_snr_points,
        points,
        threshold=harmonic_config.harmony_magnitude_threshold,
        required_peak_count=harmonic_config.harmony_peak_count,
        median=median,
        timestamps=[float(timestamp_ms) for timestamp_ms in sorted_timestamps_ms],
        use_dynamic_eval=harmonic_config.use_dynamic_harmonic_eval,
        use_phase_check=harmonic_config.use_phase_in_harmonic_check,
        phase_similarity_threshold=harmonic_config.phase_similarity_threshold,
        harmonic_tolerance_ratio=harmonic_config.harmonic_tolerance_ratio,
    )

    # # Prune sub-harmonic duplicates: if a large-period point has matching
    # # smaller-period peaks at exact integer divisors (within tolerance),
    # # remove the smaller / derived points so we report the canonical period.
    # if harmonic_points:
    #     pts = sorted(harmonic_points, key=lambda p: p[0], reverse=True)

    #     changed = True
    #     # Check divisors up to 6th harmonic (adjustable). Restart loop when a
    #     # removal happens to ensure transitive relationships are cleaned.
    #     while changed:
    #         changed = False
    #         for i, (base_period, _base_mag) in enumerate(pts):
    #             for n in range(2, 7):
    #                 target = base_period / n
    #                 # relative tolerance: 2% or at least 1ms
    #                 tol = max(1.0, target * 0.02)
    #                 # search for a point near the target (exclude the base itself)
    #                 found_index = None
    #                 for j, (p_period, _p_mag) in enumerate(pts):
    #                     if j == i:
    #                         continue
    #                     if abs(p_period - target) <= tol:
    #                         found_index = j
    #                         break

    #                 if found_index is not None:
    #                     # remove the derived (smaller) period so the base remains
    #                     del pts[found_index]
    #                     changed = True
    #                     break
    #             if changed:
    #                 break

    #     harmonic_points = sorted(pts, key=lambda p: p[0])
    # else:
    #     harmonic_points = []

    # Generate a graph of the transform and mark important points when requested.
    plot_path = None
    if context.plot:
        try:
            plot_path = plot_fourier_points(
                period_candidates_ms,
                magnitudes,
                top_percent_points=top_percent_points,
                high_snr_points=high_snr_points,
                harmonic_points=harmonic_points,
                median=median,
                mad=mad,
                snr_threshold=harmonic_config.snr_threshold,
                endpoint_id=context.endpoint_id,
                native_event_id=context.native_event_id,
            )
        except Exception:
            plot_path = None

    timestamp_floats = [float(timestamp_ms) for timestamp_ms in sorted_timestamps_ms]
    harmonic_points = rerank_harmonic_peaks_by_spacing(harmonic_points, timestamp_floats)
    alerts = []
    for point in harmonic_points:
        fourier_period_ms = float(point[0])
        period_ms = fourier_period_ms
        peak_magnitude = float(point[1])
        phase = float(phase_by_period.get(period_ms, math.nan))

        canonical = resolve_superharmonic_canonical_period(
            period_ms,
            peak_magnitude,
            timestamp_floats,
            phase=phase,
        )
        if canonical.canonicalized:
            period_ms = canonical.period_ms
            peak_magnitude = canonical.peak_magnitude
            phase = canonical.phase
            phase_by_period[period_ms] = phase

        alias = resolve_subharmonic_alias_period(
            period_ms,
            peak_magnitude,
            timestamp_floats,
            phase=phase,
        )
        if alias.canonicalized:
            period_ms = alias.period_ms
            peak_magnitude = alias.peak_magnitude
            phase = alias.phase
            phase_by_period[period_ms] = phase

        if not period_supported_by_event_spacing(period_ms, timestamp_floats):
            continue

        spacing_coherence = compute_spacing_coherence(period_ms, timestamp_floats)
        median_gap_fit = compute_period_median_gap_fit(period_ms, timestamp_floats)
        spacing_selection_score = compute_spacing_selection_score(period_ms, timestamp_floats)

        window_breakdown = compute_window_confidence(
            period_ms,
            peak_magnitude,
            median,
            mad,
            points,
            timestamp_floats,
            phase_by_period,
        )
        snapshot = build_window_snapshot(
            ts_begin=sorted_timestamps_ms[0],
            ts_end=sorted_timestamps_ms[-1],
            period_ts=period_ms,
            phase=phase,
            breakdown=window_breakdown,
        )
        confidence_breakdown = ConfidenceBreakdown(level="window", window=window_breakdown)

        if CONFIDENCE_SCORING_CONFIG.confidence_logging_enabled:
            log_confidence_event(
                "window_detection",
                {
                    "endpoint_id": context.endpoint_id,
                    "native_event_id": context.native_event_id,
                    "fourier_period_ms": fourier_period_ms,
                    "chosen_period_ms": period_ms,
                    "period_ms": period_ms,
                    "superharmonic_from_period_ms": canonical.original_period_ms,
                    "superharmonic_reason": canonical.reason,
                    "subharmonic_from_period_ms": alias.original_period_ms,
                    "subharmonic_reason": alias.reason,
                    "spacing_coherence": spacing_coherence,
                    "median_gap_fit": median_gap_fit,
                    "spacing_selection_score": spacing_selection_score,
                    "ts_begin": sorted_timestamps_ms[0],
                    "ts_end": sorted_timestamps_ms[-1],
                    "confidence": finalize_uncorroborated_window_confidence(window_breakdown),
                    "breakdown": confidence_breakdown.to_dict(),
                },
            )

        alerts.append(
            AlertCore(
                ts_begin=sorted_timestamps_ms[0],
                ts_end=sorted_timestamps_ms[-1],
                period_ts=period_ms,
                confidence=finalize_uncorroborated_window_confidence(window_breakdown),
                phase=phase,
                window_snapshots=(snapshot,),
                confidence_breakdown=confidence_breakdown,
            )
        )
    return alerts

register_alert_builder("fourier", build_fourier_alert_from_sorted_timestamps_ms)


def build_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    endpoint_id: str,
    native_event_id: int,
    period_candidates_ms: list[float] | None = None,
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> List[AlertCore] | None:
    builder = get_alert_builder(method)
    context = AlertBuildContext(
        endpoint_id=endpoint_id,
        native_event_id=native_event_id,
        period_candidates_ms=period_candidates_ms,
        plot=plot,
        show_progress=show_progress,
    )
    if method == "fourier":
        return _build_windowed_fourier_alerts_from_sorted_timestamps_ms(sorted_timestamps_ms, context)

    return builder(sorted_timestamps_ms, context)


def _preserved_alert_record_to_alert_core(record: AlertRecord) -> AlertCore:
    from confidence_scoring import WindowConfidenceBreakdown

    breakdown = WindowConfidenceBreakdown(
        peak_magnitude=0.0,
        median=0.0,
        mad=0.0,
        snr=0.0,
        base_peak=float(record.confidence),
        snr_bonus=0.0,
        harmonic_bonus=0.0,
        phase_bonus=0.0,
        window_confidence=record.confidence,
    )
    snapshot = build_window_snapshot(
        ts_begin=record.ts_begin,
        ts_end=record.ts_end,
        period_ts=record.period_ts,
        phase=record.phase,
        breakdown=breakdown,
    )
    return AlertCore(
        ts_begin=record.ts_begin,
        ts_end=record.ts_end,
        period_ts=record.period_ts,
        confidence=record.confidence,
        phase=record.phase,
        window_snapshots=(snapshot,),
    )


def _finalize_alert_record_from_core(
    endpoint_id: str,
    native_event_id: int,
    alert_core: AlertCore,
    candidate_events: list[tuple[int, int]],
) -> AlertRecord | None:
    matched_events = match_events_to_alert(
        candidate_events,
        ts_begin_ms=alert_core.ts_begin,
        ts_end_ms=alert_core.ts_end,
        period_ms=alert_core.period_ts,
        phase_rad=alert_core.phase,
    )
    window_timestamps = [
        float(timestamp_ms)
        for _, timestamp_ms in candidate_events
        if alert_core.ts_begin <= timestamp_ms <= alert_core.ts_end
    ]
    spacing_score = (
        compute_spacing_selection_score(alert_core.period_ts, window_timestamps)
        if window_timestamps
        else 0.0
    )
    evidence_penalty = compute_evidence_sufficiency_penalty(
        period_ms=alert_core.period_ts,
        ts_begin_ms=alert_core.ts_begin,
        ts_end_ms=alert_core.ts_end,
        matched_count=len(matched_events),
        timestamps=window_timestamps,
    )
    final_confidence = apply_evidence_penalty(alert_core.confidence, evidence_penalty)
    if not should_publish_alert(
        final_confidence,
        events_in_window=len(window_timestamps),
        matched_count=len(matched_events),
        spacing_score=spacing_score,
    ):
        logger.info(
            "Alert suppressed endpoint=%s nativeEventID=%s periodMs=%.0f "
            "windowConf=%s finalConf=%s eventsInWindow=%s matched=%s "
            "spacingScore=%.2f evidencePenalty=%.1f",
            endpoint_id,
            native_event_id,
            alert_core.period_ts,
            alert_core.confidence,
            final_confidence,
            len(window_timestamps),
            len(matched_events),
            spacing_score,
            evidence_penalty,
        )
        return None

    return AlertRecord(
        endpoint_id=endpoint_id,
        native_event_id=native_event_id,
        matched_events=matched_events,
        ts_begin=alert_core.ts_begin,
        ts_end=alert_core.ts_end,
        period_ts=alert_core.period_ts,
        confidence=final_confidence,
        phase=alert_core.phase,
    )


def _alert_cores_to_alert_records(
    endpoint_id: str,
    native_event_id: int,
    native_events: list[EventRecord],
    alert_cores: list[AlertCore],
    seen_alert_keys: set[tuple],
) -> list[AlertRecord]:
    alerts: list[AlertRecord] = []
    candidate_events = [
        (event.internal_event_id, event.timestamp_ms)
        for event in native_events
    ]

    for alert_core in alert_cores:
        if CONFIDENCE_SCORING_CONFIG.confidence_logging_enabled and alert_core.confidence_breakdown:
            log_confidence_event(
                "final_alert",
                {
                    "endpoint_id": endpoint_id,
                    "native_event_id": native_event_id,
                    "period_ms": alert_core.period_ts,
                    "ts_begin": alert_core.ts_begin,
                    "ts_end": alert_core.ts_end,
                    "confidence": alert_core.confidence,
                    "breakdown": alert_core.confidence_breakdown.to_dict(),
                },
            )

        alert_record = _finalize_alert_record_from_core(
            endpoint_id,
            native_event_id,
            alert_core,
            candidate_events,
        )
        if alert_record is None:
            continue
        alert_key = (
            alert_record.endpoint_id,
            alert_record.native_event_id,
            alert_record.ts_begin,
            alert_record.ts_end,
            alert_record.period_ts,
            alert_record.confidence,
        )
        if alert_key in seen_alert_keys:
            continue
        seen_alert_keys.add(alert_key)
        alerts.append(alert_record)

    return alerts


def build_alerts_for_native_event_incremental(
    endpoint_id: str,
    native_event_id: int,
    native_events: list[EventRecord],
    *,
    impact_range: tuple[int, int] | None,
    preserved_alert_records: list[AlertRecord],
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> list[AlertRecord]:
    native_events = sorted(native_events, key=lambda item: item.timestamp_ms)
    sorted_timestamps_ms = [event.timestamp_ms for event in native_events]
    if len(sorted_timestamps_ms) < HARMONIC_ANALYSIS_CONFIG.min_events_for_alert:
        return []

    context = AlertBuildContext(
        endpoint_id=endpoint_id,
        native_event_id=native_event_id,
        plot=plot,
        show_progress=show_progress,
    )

    recomputed_cores: list[AlertCore] = []
    if method == "fourier":
        recomputed = _build_windowed_fourier_alerts_from_sorted_timestamps_ms(
            sorted_timestamps_ms,
            context,
            impact_range=impact_range,
        )
        if recomputed:
            recomputed_cores.extend(recomputed)
    else:
        builder = get_alert_builder(method)
        built = builder(sorted_timestamps_ms, context)
        if built is not None:
            recomputed_cores.append(built)

    recomputed_merged = suppress_harmonic_ghost_alerts(recomputed_cores)
    if recomputed_merged:
        recomputed_merged = _merge_overlapping_alert_cores(
            recomputed_merged,
            endpoint_id=endpoint_id,
            native_event_id=native_event_id,
        )

    seen_alert_keys: set[tuple] = set()
    alerts: list[AlertRecord] = []
    for record in preserved_alert_records:
        alert_key = (
            record.endpoint_id,
            record.native_event_id,
            record.ts_begin,
            record.ts_end,
            record.period_ts,
            record.confidence,
        )
        if alert_key in seen_alert_keys:
            continue
        seen_alert_keys.add(alert_key)
        alerts.append(record)

    if recomputed_merged:
        alerts.extend(
            _alert_cores_to_alert_records(
                endpoint_id,
                native_event_id,
                native_events,
                recomputed_merged,
                seen_alert_keys,
            )
        )
    return alerts


def build_alerts_for_endpoint_incremental(
    endpoint_id: str,
    events: Iterable[EventRecord],
    impacts: list,
    preserved_by_native_event: dict[int, list[AlertRecord]],
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> list[AlertRecord]:
    grouped_by_native_event = _group_logs_by_native_event(events)
    alerts: list[AlertRecord] = []

    impact_by_native_event = {impact.native_event_id: impact for impact in impacts}

    native_event_items = [
        (native_event_id, impact_by_native_event[native_event_id])
        for native_event_id in impact_by_native_event
        if native_event_id in grouped_by_native_event
    ]
    if show_progress:
        native_event_items = tqdm(
            native_event_items,
            desc=f"Processing endpoint {endpoint_id}",
            unit="event group",
        )

    for native_event_id, impact in native_event_items:
        native_events = grouped_by_native_event[native_event_id]
        impact_range = (impact.new_min_ms, impact.new_max_ms)
        preserved_records = preserved_by_native_event.get(native_event_id, [])
        alerts.extend(
            build_alerts_for_native_event_incremental(
                endpoint_id,
                native_event_id,
                native_events,
                impact_range=impact_range,
                preserved_alert_records=preserved_records,
                method=method,
                plot=plot,
                show_progress=show_progress,
            )
        )

    return alerts


def build_alerts_for_endpoint(
    endpoint_id: str,
    events: Iterable[EventRecord],
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> list[AlertRecord]:
    grouped_by_native_event = _group_logs_by_native_event(events)
    alerts = []
    seen_alert_keys: set[tuple] = set()

    native_event_items = grouped_by_native_event.items()
    if show_progress:
        native_event_items = tqdm(
            native_event_items,
            desc=f"Processing endpoint {endpoint_id}",
            unit="event group",
        )

    for native_event_id, native_events in native_event_items:
        native_events = sorted(native_events, key=lambda item: item.timestamp_ms)
        sorted_timestamps_ms = [event.timestamp_ms for event in native_events]
        alert_cores = build_alerts_from_sorted_timestamps_ms(
            sorted_timestamps_ms,
            endpoint_id=endpoint_id,
            native_event_id=native_event_id,
            method=method,
            plot=plot,
            show_progress=show_progress,
        )
        if not alert_cores:
            continue

        for alert_core in alert_cores:
            if CONFIDENCE_SCORING_CONFIG.confidence_logging_enabled and alert_core.confidence_breakdown:
                log_confidence_event(
                    "final_alert",
                    {
                        "endpoint_id": endpoint_id,
                        "native_event_id": native_event_id,
                        "period_ms": alert_core.period_ts,
                        "ts_begin": alert_core.ts_begin,
                        "ts_end": alert_core.ts_end,
                        "confidence": alert_core.confidence,
                        "breakdown": alert_core.confidence_breakdown.to_dict(),
                    },
                )

            candidate_events = [
                (event.internal_event_id, event.timestamp_ms)
                for event in native_events
            ]
            alert_record = _finalize_alert_record_from_core(
                endpoint_id,
                native_event_id,
                alert_core,
                candidate_events,
            )
            if alert_record is None:
                continue
            alert_key = (
                alert_record.endpoint_id,
                alert_record.native_event_id,
                alert_record.ts_begin,
                alert_record.ts_end,
                alert_record.period_ts,
                alert_record.confidence,
            )
            if alert_key in seen_alert_keys:
                continue
            seen_alert_keys.add(alert_key)
            alerts.append(alert_record)

    return alerts


def run_brain_for_endpoint(
    endpoint_id: str,
    fetch_events: FetchEventsFn,
    publish_alerts: PublishAlertsFn,
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> int:
    events = list(fetch_events(endpoint_id))
    alerts = build_alerts_for_endpoint(
        endpoint_id,
        events,
        method=method,
        plot=plot,
        show_progress=show_progress,
    )
    return publish_alerts(endpoint_id, alerts)
