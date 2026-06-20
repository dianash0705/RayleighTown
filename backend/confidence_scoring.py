"""
Multi-factor alert confidence scoring.

This module scores alerts in two layers:

1. **Window score** — computed for each sliding-window detection that passes the
   existing Fourier / SNR / first-harmonic filters. Filters are unchanged; only
   the numeric confidence is enriched.

2. **Group score** — computed when overlapping window detections with matching
   period are merged. Rewards repeated and consecutive sightings.

All weights and caps live in ``ConfidenceScoringConfig`` (``config.py``).

Window formula (documented for tuning discussions)
--------------------------------------------------
Let *m* be the raw peak magnitude, *median* the spectrum median, *mad* the
median absolute deviation of magnitudes, and *snr* = |m - median| / mad.

::

    base_peak     = clamp(m * base_peak_scale, 0, max_base_peak)
    snr_bonus     = max_snr_bonus * (1 - exp(-max(0, snr - snr_threshold) / snr_decay))
    harmonic_bonus = sum over k in {3,4,5,6} of:
                       weight_k * min(support(period/k) / (expected_support/k), 1)
                     scaled to max_harmonic_bonus
                     (k=2 is excluded — passing the harmony filter already implies it)
    phase_bonus   = sum over k in {2,3,4,5,6} of:
                       weight_k * max(0, cos(phase_fundamental - phase_harmonic))
                     scaled to max_phase_bonus

Shape governance (weak fundamentals):
    Unless base_peak >= all_stars_min_base_peak, harmonic+phase are capped to
    base_peak * shape_bonus_to_base_cap_ratio. SNR bonus is never capped — it
    already passed the SNR filter.

::

    window_score = clamp(base_peak + snr_bonus + harmonic_bonus + phase_bonus,
                         0, max_window_score)

Period aliasing note (e.g. 60s reported as 120s)
-------------------------------------------------
A noisy 60s fundamental can appear stronger at 2x period in Fourier space.
That is a harmonic alias, not a separate signal. Shape governance keeps alias
scores low when the fundamental peak height is weak.

Group formula
-------------
Given *n* window detections with window scores [w1..wn]:

::

    aggregate_base = median([w1..wn])
    count_bonus    = min(max_count_bonus, count_bonus_scale * log2(1 + n))
                     * min(1, mean_phase_similarity / count_bonus_phase_gate)
    streak_bonus   = only when longest_streak >= streak_min_windows (default 3):
                     min(max_streak_bonus, scale * (longest_streak - (min - 1)))
    phase_consistency_bonus =
        max_phase_consistency_bonus * mean pairwise max(0, cos(phase_i - phase_j))
    phase_penalty  = when mean_phase_similarity < phase_penalty_threshold:
                     max_phase_penalty * ((threshold - similarity) / threshold)^1.5

::

    uncorroborated cap (single window, base_peak below threshold):
        min(window_score, uncorroborated_single_window_cap)

    group_score = clamp(aggregate_base + count_bonus + streak_bonus
                        + phase_consistency_bonus - phase_penalty, 0, 100)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from config import CONFIDENCE_SCORING_CONFIG, HARMONIC_ANALYSIS_CONFIG
from fourier import _harmonic_support_at_period, _phase_similarity, compute_spacing_alias_penalty, evaluate_period


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_confidence(value: float) -> int:
    return int(round(_clamp(value, 0, 100)))


@dataclass(frozen=True)
class HarmonicContribution:
    """Per-divisor detail for harmonic / phase confidence bonuses."""

    divisor: int
    harmonic_period_ms: float
    support_ratio: float
    phase_similarity: float
    harmonic_weight: float


@dataclass(frozen=True)
class WindowConfidenceBreakdown:
    """Serializable breakdown for a single sliding-window detection."""

    peak_magnitude: float
    median: float
    mad: float
    snr: float
    base_peak: float
    snr_bonus: float
    harmonic_bonus: float
    phase_bonus: float
    shape_bonus_capped: bool = False
    harmonic_contributions: tuple[HarmonicContribution, ...] = ()
    spacing_alias_penalty: float = 0.0
    window_score: float = 0.0
    window_confidence: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["harmonic_contributions"] = [
            asdict(contribution) for contribution in self.harmonic_contributions
        ]
        return payload


@dataclass(frozen=True)
class GroupConfidenceBreakdown:
    """Serializable breakdown after merging overlapping window detections."""

    window_confidences: tuple[int, ...]
    aggregate_base: float
    median_base_peak: float
    window_count: int
    longest_streak: int
    count_bonus: float
    streak_bonus: float
    phase_consistency_bonus: float
    phase_penalty: float
    mean_phase_similarity: float
    group_score: float
    final_confidence: int
    window_breakdowns: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """Top-level breakdown attached to an alert and written to logs."""

    level: str
    window: WindowConfidenceBreakdown | None = None
    group: GroupConfidenceBreakdown | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "window": self.window.to_dict() if self.window else None,
            "group": self.group.to_dict() if self.group else None,
        }


@dataclass(frozen=True)
class WindowSnapshot:
    """Minimal per-window facts preserved through alert merging."""

    ts_begin: int
    ts_end: int
    period_ts: float
    phase: float
    window_confidence: int
    breakdown: WindowConfidenceBreakdown


def _get_phase(
    period_ms: float,
    phase_by_period: dict[float, float],
    timestamps: list[float],
    eval_cache: dict[float, tuple[float, float]],
) -> float:
    cached_phase = phase_by_period.get(period_ms)
    if cached_phase is not None and not math.isnan(cached_phase):
        return cached_phase

    cached = eval_cache.get(period_ms)
    if cached is None:
        _magnitude, phase = evaluate_period(timestamps, period_ms)
        eval_cache[period_ms] = (_magnitude, phase)
    else:
        _magnitude, phase = cached
    return phase


def _govern_shape_bonuses(
    base_peak: float,
    harmonic_bonus: float,
    phase_bonus: float,
) -> tuple[float, float, bool]:
    """
    Prevent harmonic/phase bonuses from overwhelming a weak fundamental peak.

    SNR is intentionally excluded — peaks that pass the SNR filter keep that bonus.
    """
    scoring = CONFIDENCE_SCORING_CONFIG
    shape_bonus = harmonic_bonus + phase_bonus
    if base_peak >= scoring.all_stars_min_base_peak:
        return harmonic_bonus, phase_bonus, False

    if shape_bonus <= 0:
        return harmonic_bonus, phase_bonus, False

    max_shape = base_peak * scoring.shape_bonus_to_base_cap_ratio
    if shape_bonus <= max_shape:
        return harmonic_bonus, phase_bonus, False

    scale = max_shape / shape_bonus
    return harmonic_bonus * scale, phase_bonus * scale, True


def _median_base_peak(snapshots: list[WindowSnapshot]) -> float:
    base_peaks = [snapshot.breakdown.base_peak for snapshot in snapshots]
    sorted_peaks = sorted(base_peaks)
    middle = len(sorted_peaks) // 2
    if len(sorted_peaks) % 2 == 0:
        return (sorted_peaks[middle - 1] + sorted_peaks[middle]) / 2.0
    return float(sorted_peaks[middle])


def _compute_phase_penalty(mean_phase_similarity: float, window_count: int) -> float:
    scoring = CONFIDENCE_SCORING_CONFIG
    if window_count < 2:
        return 0.0
    if mean_phase_similarity >= scoring.phase_penalty_threshold:
        return 0.0

    deficit_ratio = (
        (scoring.phase_penalty_threshold - mean_phase_similarity)
        / scoring.phase_penalty_threshold
    )
    return scoring.max_phase_penalty * (deficit_ratio ** 1.5)


def compute_snr_bonus(
    peak_magnitude: float,
    median: float,
    mad: float,
    *,
    snr_threshold: float | None = None,
) -> tuple[float, float]:
    """
    Return ``(snr, snr_bonus)``.

    SNR bonus is zero at the filter threshold and rises sub-linearly above it.
    """
    scoring = CONFIDENCE_SCORING_CONFIG
    harmonic = HARMONIC_ANALYSIS_CONFIG
    threshold = snr_threshold if snr_threshold is not None else harmonic.snr_threshold

    if mad <= 0:
        return 0.0, 0.0

    snr = abs(peak_magnitude - median) / mad
    excess = max(0.0, snr - threshold)
    bonus = scoring.max_snr_bonus * (1.0 - math.exp(-excess / scoring.snr_decay))
    return snr, bonus


def compute_harmonic_and_phase_bonuses(
    period_ms: float,
    peak_magnitude: float,
    median: float,
    all_points: list[tuple[float, float]],
    timestamps: list[float],
    phase_by_period: dict[float, float],
    *,
    use_dynamic_eval: bool | None = None,
    harmonic_tolerance_ratio: float | None = None,
) -> tuple[float, float, tuple[HarmonicContribution, ...]]:
    """
    Return ``(harmonic_bonus, phase_bonus, contributions)``.

    Magnitude harmonics use divisors 3–6 only (divisor 2 is filter-gated).
    Phase matching uses divisors 2–6.
    """
    scoring = CONFIDENCE_SCORING_CONFIG
    harmonic = HARMONIC_ANALYSIS_CONFIG
    dynamic_eval = (
        use_dynamic_eval
        if use_dynamic_eval is not None
        else harmonic.use_dynamic_harmonic_eval
    )
    tolerance = (
        harmonic_tolerance_ratio
        if harmonic_tolerance_ratio is not None
        else harmonic.harmonic_tolerance_ratio
    )

    fundamental_support = max(0.0, peak_magnitude - median)
    if fundamental_support <= 0:
        return 0.0, 0.0, ()

    eval_cache: dict[float, tuple[float, float]] = {}
    fundamental_phase = _get_phase(period_ms, phase_by_period, timestamps, eval_cache)

    harmonic_weighted = 0.0
    harmonic_weight_sum = 0.0
    phase_weighted = 0.0
    phase_weight_sum = 0.0
    contributions: list[HarmonicContribution] = []

    for divisor in scoring.confidence_harmonic_divisors:
        harmonic_weight = scoring.harmonic_weights.get(divisor, 0.0)
        phase_weight = scoring.phase_weights.get(divisor, 0.0)
        if harmonic_weight <= 0 and phase_weight <= 0:
            continue

        harmonic_period = period_ms / divisor
        support = _harmonic_support_at_period(
            all_points,
            harmonic_period,
            median,
            timestamps,
            use_dynamic_eval=dynamic_eval,
            harmonic_tolerance_ratio=tolerance,
            eval_cache=eval_cache,
        )
        expected_support = fundamental_support / divisor
        support_ratio = _clamp(support / expected_support, 0.0, 1.0) if expected_support > 0 else 0.0

        harmonic_phase = _get_phase(harmonic_period, phase_by_period, timestamps, eval_cache)
        phase_similarity = max(0.0, _phase_similarity(fundamental_phase, harmonic_phase))

        if divisor >= 3 and harmonic_weight > 0:
            harmonic_weighted += harmonic_weight * support_ratio
            harmonic_weight_sum += harmonic_weight

        if phase_weight > 0:
            phase_weighted += phase_weight * phase_similarity
            phase_weight_sum += phase_weight

        contributions.append(
            HarmonicContribution(
                divisor=divisor,
                harmonic_period_ms=harmonic_period,
                support_ratio=support_ratio,
                phase_similarity=phase_similarity,
                harmonic_weight=harmonic_weight,
            )
        )

    harmonic_bonus = 0.0
    if harmonic_weight_sum > 0:
        harmonic_bonus = scoring.max_harmonic_bonus * (harmonic_weighted / harmonic_weight_sum)

    phase_bonus = 0.0
    if phase_weight_sum > 0:
        phase_bonus = scoring.max_phase_bonus * (phase_weighted / phase_weight_sum)

    return harmonic_bonus, phase_bonus, tuple(contributions)


def compute_window_confidence(
    period_ms: float,
    peak_magnitude: float,
    median: float,
    mad: float,
    all_points: list[tuple[float, float]],
    timestamps: list[float],
    phase_by_period: dict[float, float],
) -> WindowConfidenceBreakdown:
    """Score a single window detection that already passed all filters."""
    scoring = CONFIDENCE_SCORING_CONFIG

    base_peak = _clamp(peak_magnitude * scoring.base_peak_scale, 0.0, scoring.max_base_peak)
    snr, snr_bonus = compute_snr_bonus(peak_magnitude, median, mad)
    harmonic_bonus, phase_bonus, contributions = compute_harmonic_and_phase_bonuses(
        period_ms,
        peak_magnitude,
        median,
        all_points,
        timestamps,
        phase_by_period,
    )
    harmonic_bonus, phase_bonus, shape_bonus_capped = _govern_shape_bonuses(
        base_peak,
        harmonic_bonus,
        phase_bonus,
    )

    spacing_alias_penalty = compute_spacing_alias_penalty(period_ms, timestamps)

    window_score = _clamp(
        base_peak + snr_bonus + harmonic_bonus + phase_bonus - spacing_alias_penalty,
        0.0,
        scoring.max_window_score,
    )

    return WindowConfidenceBreakdown(
        peak_magnitude=peak_magnitude,
        median=median,
        mad=mad,
        snr=snr,
        base_peak=base_peak,
        snr_bonus=snr_bonus,
        harmonic_bonus=harmonic_bonus,
        phase_bonus=phase_bonus,
        shape_bonus_capped=shape_bonus_capped,
        harmonic_contributions=contributions,
        spacing_alias_penalty=spacing_alias_penalty,
        window_score=window_score,
        window_confidence=_round_confidence(window_score),
    )


def _estimate_window_step_ms(snapshots: list[WindowSnapshot]) -> int:
    if len(snapshots) < 2:
        return 0

    sorted_snapshots = sorted(snapshots, key=lambda item: item.ts_begin)
    deltas = [
        sorted_snapshots[index + 1].ts_begin - sorted_snapshots[index].ts_begin
        for index in range(len(sorted_snapshots) - 1)
        if sorted_snapshots[index + 1].ts_begin > sorted_snapshots[index].ts_begin
    ]
    if not deltas:
        return 0

    deltas.sort()
    middle = len(deltas) // 2
    if len(deltas) % 2 == 0:
        return int(round((deltas[middle - 1] + deltas[middle]) / 2))
    return int(round(deltas[middle]))


def _longest_consecutive_streak(snapshots: list[WindowSnapshot]) -> int:
    if not snapshots:
        return 0
    if len(snapshots) == 1:
        return 1

    sorted_snapshots = sorted(snapshots, key=lambda item: item.ts_begin)
    step_ms = _estimate_window_step_ms(sorted_snapshots)
    if step_ms <= 0:
        return 1

    tolerance = max(1, int(step_ms * CONFIDENCE_SCORING_CONFIG.streak_step_tolerance_ratio))
    longest = 1
    streak = 1
    for index in range(1, len(sorted_snapshots)):
        delta = sorted_snapshots[index].ts_begin - sorted_snapshots[index - 1].ts_begin
        if abs(delta - step_ms) <= tolerance:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 1
    return longest


def _mean_pairwise_phase_similarity(snapshots: list[WindowSnapshot]) -> float:
    phases = [snapshot.phase for snapshot in snapshots if not math.isnan(snapshot.phase)]
    if len(phases) < 2:
        return 0.0

    similarities: list[float] = []
    for left_index in range(len(phases)):
        for right_index in range(left_index + 1, len(phases)):
            similarities.append(max(0.0, _phase_similarity(phases[left_index], phases[right_index])))

    return sum(similarities) / len(similarities)


def _apply_single_window_penalty(score: float, base_peak: float) -> float:
    scoring = CONFIDENCE_SCORING_CONFIG
    penalized = max(0.0, score - scoring.single_window_confidence_penalty)
    if base_peak < scoring.uncorroborated_single_window_base_threshold:
        penalized = min(penalized, scoring.uncorroborated_single_window_cap)
    return penalized


def compute_group_confidence(snapshots: list[WindowSnapshot]) -> GroupConfidenceBreakdown:
    """Aggregate window scores into a merged alert confidence."""
    scoring = CONFIDENCE_SCORING_CONFIG

    if not snapshots:
        empty = GroupConfidenceBreakdown(
            window_confidences=(),
            aggregate_base=0.0,
            median_base_peak=0.0,
            window_count=0,
            longest_streak=0,
            count_bonus=0.0,
            streak_bonus=0.0,
            phase_consistency_bonus=0.0,
            phase_penalty=0.0,
            mean_phase_similarity=0.0,
            group_score=0.0,
            final_confidence=0,
        )
        return empty

    if len(snapshots) == 1:
        only = snapshots[0]
        lone_score = _apply_single_window_penalty(float(only.window_confidence), only.breakdown.base_peak)
        return GroupConfidenceBreakdown(
            window_confidences=(only.window_confidence,),
            aggregate_base=lone_score,
            median_base_peak=only.breakdown.base_peak,
            window_count=1,
            longest_streak=1,
            count_bonus=0.0,
            streak_bonus=0.0,
            phase_consistency_bonus=0.0,
            phase_penalty=0.0,
            mean_phase_similarity=0.0,
            group_score=lone_score,
            final_confidence=_round_confidence(lone_score),
            window_breakdowns=(only.breakdown.to_dict(),),
        )

    window_confidences = tuple(snapshot.window_confidence for snapshot in snapshots)
    sorted_scores = sorted(window_confidences)
    middle = len(sorted_scores) // 2
    if len(sorted_scores) % 2 == 0:
        aggregate_base = (sorted_scores[middle - 1] + sorted_scores[middle]) / 2.0
    else:
        aggregate_base = float(sorted_scores[middle])

    window_count = len(snapshots)
    longest_streak = _longest_consecutive_streak(snapshots)
    mean_phase_similarity = _mean_pairwise_phase_similarity(snapshots)
    median_base_peak = _median_base_peak(snapshots)

    raw_count_bonus = min(
        scoring.max_count_bonus,
        scoring.count_bonus_scale * math.log2(1.0 + window_count),
    )
    phase_gate = max(scoring.count_bonus_phase_gate, 1e-6)
    count_bonus = raw_count_bonus * min(1.0, mean_phase_similarity / phase_gate)

    streak_bonus = 0.0
    if longest_streak >= scoring.streak_min_windows:
        streak_bonus = min(
            scoring.max_streak_bonus,
            scoring.streak_bonus_scale * (longest_streak - (scoring.streak_min_windows - 1)),
        )

    phase_consistency_bonus = scoring.max_phase_consistency_bonus * mean_phase_similarity
    phase_penalty = _compute_phase_penalty(mean_phase_similarity, window_count)

    group_score = _clamp(
        aggregate_base
        + count_bonus
        + streak_bonus
        + phase_consistency_bonus
        - phase_penalty,
        0.0,
        100.0,
    )

    return GroupConfidenceBreakdown(
        window_confidences=window_confidences,
        aggregate_base=aggregate_base,
        median_base_peak=median_base_peak,
        window_count=window_count,
        longest_streak=longest_streak,
        count_bonus=count_bonus,
        streak_bonus=streak_bonus,
        phase_consistency_bonus=phase_consistency_bonus,
        phase_penalty=phase_penalty,
        mean_phase_similarity=mean_phase_similarity,
        group_score=group_score,
        final_confidence=_round_confidence(group_score),
        window_breakdowns=tuple(snapshot.breakdown.to_dict() for snapshot in snapshots),
    )


def finalize_uncorroborated_window_confidence(breakdown: WindowConfidenceBreakdown) -> int:
    """
    Cap lone window scores that lack merge corroboration.

    Strong fundamentals (high base_peak) keep most of the window score after a
    fixed single-window penalty.
    """
    penalized = _apply_single_window_penalty(float(breakdown.window_confidence), breakdown.base_peak)
    return _round_confidence(penalized)


def compute_evidence_sufficiency_penalty(
    *,
    period_ms: float,
    ts_begin_ms: int,
    ts_end_ms: int,
    matched_count: int,
    timestamps: list[float],
) -> float:
    """
    Penalize alerts with too few in-window events or weak spacing support for the period.

    Uses raw event count in the alert window for density (not grid-matched count), so
    Fourier-detected windows are not over-penalized when phase alignment is slightly off.
    """
    from fourier import compute_spacing_selection_score

    scoring = CONFIDENCE_SCORING_CONFIG
    if not math.isfinite(period_ms) or period_ms <= 0:
        return scoring.max_evidence_penalty

    window_span_ms = max(0, int(ts_end_ms) - int(ts_begin_ms))
    if window_span_ms <= 0:
        return scoring.max_evidence_penalty

    events_in_window = len(timestamps)
    penalty = 0.0

    if events_in_window < scoring.min_matched_events_for_publish:
        shortfall = scoring.min_matched_events_for_publish - events_in_window
        penalty += min(30.0, shortfall * 12.0)

    expected_ticks = max(1.0, (window_span_ms / period_ms) + 1.0)
    density_ratio = events_in_window / expected_ticks
    if density_ratio < scoring.min_evidence_density_ratio:
        severity = 1.0 - (density_ratio / scoring.min_evidence_density_ratio)
        penalty += severity * 22.0

    if timestamps:
        spacing_score = compute_spacing_selection_score(period_ms, timestamps)
        penalty += (1.0 - spacing_score) * 18.0
        if matched_count < scoring.min_matched_events_for_publish:
            grid_shortfall = scoring.min_matched_events_for_publish - matched_count
            penalty += min(12.0, grid_shortfall * 4.0) * max(0.0, 1.0 - spacing_score)

    return min(scoring.max_evidence_penalty, penalty)


def apply_evidence_penalty(confidence: int, penalty: float) -> int:
    return _round_confidence(max(0.0, float(confidence) - penalty))


def should_publish_alert(
    confidence: int,
    *,
    events_in_window: int,
    matched_count: int,
    spacing_score: float,
) -> bool:
    scoring = CONFIDENCE_SCORING_CONFIG
    if events_in_window < scoring.min_matched_events_for_publish:
        return False
    if confidence < scoring.min_publish_confidence:
        return False
    if matched_count >= scoring.min_matched_events_for_publish:
        return True
    if matched_count >= 1 and spacing_score >= scoring.min_spacing_score_for_publish:
        return True
    return False


def build_window_snapshot(
    *,
    ts_begin: int,
    ts_end: int,
    period_ts: float,
    phase: float,
    breakdown: WindowConfidenceBreakdown,
) -> WindowSnapshot:
    return WindowSnapshot(
        ts_begin=ts_begin,
        ts_end=ts_end,
        period_ts=period_ts,
        phase=phase,
        window_confidence=breakdown.window_confidence,
        breakdown=breakdown,
    )
