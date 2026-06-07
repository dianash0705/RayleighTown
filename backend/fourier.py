import math
import random
from datetime import datetime
from dataclasses import dataclass
from bisect import bisect_left

import numpy as np

from tqdm.auto import tqdm

from config import CANDIDATE_PERIOD_GROUP_CONFIGS

RADIUS = 15_000
PERCENTILE = 0.10
SMALLEST_PERIOD_MS = 2_000 # in milliseconds, needs to be greater than the expected JITTER
SMALLEST_APPEARANCE_COUNT = 4 # idk once a week
NUMBER_OF_DIFFERENT_PERIODS = 300
MAX_CANDIDATE_PERIOD_MS = None


@dataclass(frozen=True)
class CandidatePeriodGroup:
    name: str
    window_size_ms: int
    periods_ms: list[float]

RANDOM_SEED = 7
NOISE_EVENT_COUNT = 220
TIME_RANGE_MS = (0, 24 * 60 * 60 * 1000)  # 24 hours in milliseconds
PRIMARY_PERIOD_MS = 3_600_000
SECONDARY_PERIOD_MS = 1_080_000
REPEATING_SERIES_PERIOD_MS = 900_000
REPEATING_SERIES_PHASE_MS = 120_000
MAX_JITTER_MS = 10_000

def get_median_value(values: list[float]) -> float:
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n % 2 == 0:
        return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2
    else:
        return sorted_values[n // 2]

def filter_by_snr(points: list[tuple[float, float]], median: float, mad: float, min_snr: float) -> list[tuple[float, float]]:
    if mad == 0:
        return []

    snr_values = []
    for point in points:
        snr = abs(point[1] - median) / mad
        snr_values.append((point, snr))

    return [point for point, snr in snr_values if snr >= min_snr]


def _calculate_fourier_components(timestamps: list[float], period_ms: float) -> tuple[float, float, float, float]:
    sum_x = 0.0
    sum_y = 0.0
    for time_of_event in timestamps:
        alpha = (time_of_event % period_ms) / period_ms * (2 * math.pi)
        sum_x += math.cos(alpha)
        sum_y += math.sin(alpha)

    avg_x = sum_x / len(timestamps)
    avg_y = sum_y / len(timestamps)
    magnitude = math.sqrt(avg_y**2 + avg_x**2)
    phase = math.atan2(avg_y, avg_x)
    return avg_x, avg_y, magnitude, phase


def evaluate_period(timestamps: list[float], period_ms: float) -> tuple[float, float]:
    """Return (magnitude, phase) for an arbitrary candidate period."""
    _avg_x, _avg_y, magnitude, phase = _calculate_fourier_components(timestamps, period_ms)
    return magnitude, phase


def _phase_similarity(left_phase: float, right_phase: float) -> float:
    return math.cos(left_phase - right_phase)


def _is_harmonic_period(base_period_ms: float, candidate_period_ms: float, tolerance_ratio: float) -> bool:
    if candidate_period_ms <= 0:
        return False

    ratio = base_period_ms / candidate_period_ms
    nearest_multiplier = round(ratio)
    if nearest_multiplier < 2:
        return False

    return abs(ratio - nearest_multiplier) <= tolerance_ratio


def _estimate_harmonic_support(all_points: list[tuple[float, float]], target_period: float, median: float) -> float:
    if not all_points:
        return 0.0

    if target_period <= all_points[0][0]:
        return max(0.0, all_points[0][1] - median)
    if target_period >= all_points[-1][0]:
        return max(0.0, all_points[-1][1] - median)

    periods = [point[0] for point in all_points]
    right_index = bisect_left(periods, target_period)
    left_index = right_index - 1

    left_period, left_magnitude = all_points[left_index]
    right_period, right_magnitude = all_points[right_index]

    if target_period == left_period:
        return max(0.0, left_magnitude - median)
    if target_period == right_period:
        return max(0.0, right_magnitude - median)

    left_support = max(0.0, left_magnitude - median)
    right_support = max(0.0, right_magnitude - median)
    return left_support + right_support


def _grid_period_match_tolerance(target_period_ms: float, tolerance_ratio: float) -> float:
    return max(1.0, target_period_ms * tolerance_ratio)


def _grid_has_period(
    all_points: list[tuple[float, float]],
    target_period_ms: float,
    tolerance_ratio: float,
) -> bool:
    tolerance_ms = _grid_period_match_tolerance(target_period_ms, tolerance_ratio)
    for period_ms, _magnitude in all_points:
        if abs(period_ms - target_period_ms) <= tolerance_ms:
            return True
    return False


def _harmonic_support_at_period(
    all_points: list[tuple[float, float]],
    target_period_ms: float,
    median: float,
    timestamps: list[float] | None,
    *,
    use_dynamic_eval: bool,
    harmonic_tolerance_ratio: float,
    eval_cache: dict[float, tuple[float, float]] | None = None,
) -> float:
    if timestamps and use_dynamic_eval:
        cached = eval_cache.get(target_period_ms) if eval_cache is not None else None
        if cached is None:
            magnitude, phase = evaluate_period(timestamps, target_period_ms)
            if eval_cache is not None:
                eval_cache[target_period_ms] = (magnitude, phase)
        else:
            magnitude, _phase = cached
        return max(0.0, magnitude - median)

    if timestamps and not _grid_has_period(all_points, target_period_ms, harmonic_tolerance_ratio):
        cached = eval_cache.get(target_period_ms) if eval_cache is not None else None
        if cached is None:
            magnitude, phase = evaluate_period(timestamps, target_period_ms)
            if eval_cache is not None:
                eval_cache[target_period_ms] = (magnitude, phase)
        else:
            magnitude, _phase = cached
        return max(0.0, magnitude - median)

    return _estimate_harmonic_support(all_points, target_period_ms, median)


def filter_by_harmony(
    points: list[tuple[float, float]],
    all_points: list[tuple[float, float]],
    threshold: float,
    required_peak_count: int,
    median: float,
    timestamps: list[float] | None = None,
    *,
    use_dynamic_eval: bool = True,
    use_phase_check: bool = False,
    phase_similarity_threshold: float = 0.9,
    harmonic_tolerance_ratio: float = 0.05,
) -> list[tuple[float, float]]:
    if not points:
        return []

    all_xs = [p[0] for p in all_points]
    first_x = all_xs[0]
    eval_cache: dict[float, tuple[float, float]] = {}

    harmonic_points = []
    for point in points:
        period = point[0]
        magnitude = point[1] - median

        is_valid = True
        fundamental_phase = None
        if use_phase_check and timestamps:
            fundamental_phase = eval_cache.get(period, (None, None))[1]
            if fundamental_phase is None:
                _magnitude, fundamental_phase = evaluate_period(timestamps, period)
                eval_cache[period] = (_magnitude, fundamental_phase)

        for multiplier in range(2, required_peak_count + 2):
            required_peak_height = magnitude / multiplier
            harmonic_period = period / multiplier
            if harmonic_period < first_x:
                if timestamps and use_dynamic_eval:
                    pass
                else:
                    is_valid = False
                    break
            harmonic_support = _harmonic_support_at_period(
                all_points,
                harmonic_period,
                median,
                timestamps,
                use_dynamic_eval=use_dynamic_eval,
                harmonic_tolerance_ratio=harmonic_tolerance_ratio,
                eval_cache=eval_cache,
            )
            if harmonic_support < required_peak_height * threshold:
                is_valid = False
                break

            if use_phase_check and timestamps and fundamental_phase is not None:
                harmonic_phase = eval_cache.get(harmonic_period, (None, None))[1]
                if harmonic_phase is None:
                    _magnitude, harmonic_phase = evaluate_period(timestamps, harmonic_period)
                    eval_cache[harmonic_period] = (_magnitude, harmonic_phase)
                if _phase_similarity(fundamental_phase, harmonic_phase) < phase_similarity_threshold:
                    is_valid = False
                    break

        if is_valid:
            harmonic_points.append(point)

    return harmonic_points


def suppress_phase_matched_ghost_peaks(
    points: list[tuple[float, float]],
    timestamps: list[float],
    phase_similarity_threshold: float = 0.9,
    harmonic_tolerance_ratio: float = 0.05,
) -> list[tuple[float, float]]:
    if not points:
        return []

    phase_cache: dict[float, float] = {}

    def get_phase(period_ms: float) -> float:
        cached_phase = phase_cache.get(period_ms)
        if cached_phase is not None:
            return cached_phase

        _avg_x, _avg_y, _magnitude, phase = _calculate_fourier_components(timestamps, period_ms)
        phase_cache[period_ms] = phase
        return phase

    kept_points: list[tuple[float, float]] = []
    ordered_points = sorted(points, key=lambda point: point[0], reverse=True)

    for point in ordered_points:
        candidate_period_ms = point[0]
        candidate_phase = get_phase(candidate_period_ms)

        suppress_candidate = False
        for kept_point in kept_points:
            kept_period_ms = kept_point[0]
            if not _is_harmonic_period(kept_period_ms, candidate_period_ms, harmonic_tolerance_ratio):
                continue

            kept_phase = get_phase(kept_period_ms)
            if _phase_similarity(candidate_phase, kept_phase) >= phase_similarity_threshold:
                suppress_candidate = True
                break

        if not suppress_candidate:
            kept_points.append(point)

    return kept_points

def get_candidate_periods_ms2(time_range_ms: float) -> list[float]:
    largest_period_ms = time_range_ms / SMALLEST_APPEARANCE_COUNT
    if largest_period_ms <= SMALLEST_PERIOD_MS:
        raise ValueError("time range is too small to build candidate periods")

    difference = (largest_period_ms - SMALLEST_PERIOD_MS) / NUMBER_OF_DIFFERENT_PERIODS
    periods_ms = [
        (SMALLEST_PERIOD_MS + (n * difference))
        for n in range(NUMBER_OF_DIFFERENT_PERIODS)
    ]
    return periods_ms


def get_candidate_period_groups_ms(time_range_ms: float) -> list[CandidatePeriodGroup]:
    largest_period_ms = time_range_ms / SMALLEST_APPEARANCE_COUNT
    if MAX_CANDIDATE_PERIOD_MS is not None:
        largest_period_ms = min(largest_period_ms, MAX_CANDIDATE_PERIOD_MS)

    if largest_period_ms <= SMALLEST_PERIOD_MS:
        raise ValueError("time range is too small to build candidate periods")

    def append_range(periods: list[float], start_ms: int, end_ms: float, step_ms: int) -> None:
        current_ms = max(start_ms, SMALLEST_PERIOD_MS)
        while current_ms <= end_ms:
            periods.append(float(current_ms))
            current_ms += step_ms

    groups: list[CandidatePeriodGroup] = []

    for group_config in CANDIDATE_PERIOD_GROUP_CONFIGS:
        periods_ms: list[float] = []
        append_range(
            periods_ms,
            group_config.start_ms,
            min(largest_period_ms, group_config.end_ms),
            group_config.step_ms,
        )
        if periods_ms:
            groups.append(
                CandidatePeriodGroup(
                    name=group_config.name,
                    window_size_ms=group_config.window_size_ms,
                    periods_ms=periods_ms,
                )
            )

    return groups


def get_candidate_periods_ms(time_range_ms: float) -> list[float]:
    groups = get_candidate_period_groups_ms(time_range_ms)
    unique_periods_ms = sorted({period for group in groups for period in group.periods_ms})
    return unique_periods_ms


def fourier_transform(
    timestamps: list[float],
    period_candidates_ms: list[float] | None = None,
    show_progress: bool = False,
    include_phase: bool = False,
):
    if len(timestamps) < 2:
        raise ValueError("timestamps must contain at least two values")

    time_range_ms = timestamps[-1] - timestamps[0]
    periods_ms = period_candidates_ms if period_candidates_ms is not None else get_candidate_periods_ms(time_range_ms)

    point_xs = []
    point_ys = []
    point_phases = []
    period_iterator = tqdm(
        periods_ms,
        desc="Fourier transform",
        unit="period",
        disable=not show_progress,
    )
    for period_ms in period_iterator:
        _avg_x, _avg_y, distance, phase = _calculate_fourier_components(timestamps, period_ms)
        point_xs.append(period_ms)
        point_ys.append(distance)
        if include_phase:
            point_phases.append(phase)
    
    if include_phase:
        return point_xs, point_ys, point_phases

    return point_xs, point_ys

def find_threshold(points: list[tuple[float, float]], percentile: float):
    if not points:
        raise ValueError("points cannot be empty")

    percentile_value = percentile * 100 if 0 <= percentile <= 1 else percentile
    threshold = np.percentile([point[1] for point in points], percentile_value)
    return threshold

def filter_top_percent(points: list[tuple[float, float]], top_percent: float = 0.05):
    if not points:
        return []
    if not (0 < top_percent <= 1):
        raise ValueError("top_percent must be in (0, 1]")

    # Top 5% means points >= 95th percentile.
    threshold = find_threshold(points, 1 - top_percent)
    return [point for point in points if point[1] >= threshold]

def local_max_suppression(radius: float, local_maxs: list[tuple[float, float]]):
    if radius < 0:
        raise ValueError("radius must be >= 0")

    if not local_maxs:
        return []

    if len(local_maxs) == 1:
        return [local_maxs[0]]

    remaining_indices = set(range(len(local_maxs)))
    kept_indices = []

    while remaining_indices:
        best_idx = max(remaining_indices, key=lambda i: (local_maxs[i][1], -i))
        best_x = local_maxs[best_idx][0]
        kept_indices.append(best_idx)

        to_remove = {
            i for i in remaining_indices
            if abs(local_maxs[i][0] - best_x) <= radius
        }
        remaining_indices.difference_update(to_remove)

    kept_indices.sort()
    return [local_maxs[i] for i in kept_indices]

def finding_max(ys: list[float]):
    if len(ys) == 0:
        return list()
    
    if len(ys) == 1:
        return [0]

    found_maxs = list()
    first_index = 0
    if ys[first_index] > ys[first_index + 1]:
        found_maxs.append(first_index)

    last_index = len(ys) - 1
    if ys[last_index] > ys[last_index - 1]:
        found_maxs.append(last_index)

    for x in range(1, last_index):
        if ys[x] > ys[x - 1] and ys[x] > ys[x + 1]:
            found_maxs.append(x)
    
    return found_maxs

def generate_fake_timestamps_ms() -> list[float]:
    random.seed(RANDOM_SEED)

    start_ms, end_ms = TIME_RANGE_MS
    timestamps: list[int] = []

    for _ in range(NOISE_EVENT_COUNT):
        timestamps.append(random.randint(start_ms, end_ms))

    for base_ts in range(start_ms, end_ms, PRIMARY_PERIOD_MS):
        jitter = random.randint(-MAX_JITTER_MS, MAX_JITTER_MS)
        timestamps.append(base_ts + jitter)

    for base_ts in range(start_ms, end_ms, SECONDARY_PERIOD_MS):
        jitter = random.randint(-MAX_JITTER_MS // 2, MAX_JITTER_MS // 2)
        timestamps.append(base_ts + jitter)

    # Add one exact periodic signal with no jitter so a true repeating series is present.
    for base_ts in range(start_ms + REPEATING_SERIES_PHASE_MS, end_ms, REPEATING_SERIES_PERIOD_MS):
        timestamps.append(base_ts)

    timestamps = [ts for ts in timestamps if start_ms <= ts <= end_ms]
    timestamps.sort()
    return [float(ts) for ts in timestamps]

def test():
    import matplotlib.pyplot as plt
    timestamps_ms = generate_fake_timestamps_ms()
    xs, ys = fourier_transform(timestamps_ms, show_progress=True)

    points = [(float(xs[i]), float(ys[i])) for i in range(len(ys))]
    all_local_max_indices = finding_max(ys)
    local_max_points = [points[index] for index in all_local_max_indices]

    # Local-max-first flow: find local maxima, suppress nearby maxima,
    # then keep only the strongest percentile from the suppressed set.
    suppressed_local_max_points = local_max_suppression(radius=RADIUS, local_maxs=local_max_points)
    top_percent_suppressed_local_max_points = filter_top_percent(
        suppressed_local_max_points,
        top_percent=PERCENTILE,
    )


    plt.plot(xs, ys, label='Fourier magnitude from fake timestamps (ms)')

    plt.scatter(
        [point[0] for point in local_max_points],
        [point[1] for point in local_max_points],
        color='deepskyblue',
        marker='*',
        s=110,
        alpha=0.50,
        edgecolors='black',
        linewidths=0.6,
        label='all local maxima',
        zorder=2,
    )
    plt.scatter(
        [point[0] for point in suppressed_local_max_points],
        [point[1] for point in suppressed_local_max_points],
        color='red',
        marker='x',
        s=80,
        linewidths=1.4,
        label='kept after suppression',
        zorder=3,
    )
    plt.scatter(
        [point[0] for point in top_percent_suppressed_local_max_points],
        [point[1] for point in top_percent_suppressed_local_max_points],
        facecolors='none',
        edgecolors='orange',
        marker='o',
        s=220,
        linewidths=2.4,
        label='top percentile after suppression',
        zorder=5,
    )
    plt.title('Fourier Magnitudes From Synthetic Event Timestamps')
    plt.xlabel('Candidate period (milliseconds)')
    plt.ylabel('Magnitude')
    plt.legend()
    plt.show()


def plot_fourier_points(periods: list[float], magnitudes: list[float],
                        top_percent_points: list[tuple[float, float]] | None = None,
                        high_snr_points: list[tuple[float, float]] | None = None,
                        harmonic_points: list[tuple[float, float]] | None = None,
                        median: float | None = None,
                        mad: float | None = None,
                        snr_threshold: float | None = None,
                        endpoint_id: str | None = None,
                        native_event_id: int | None = None,
                        out_path: str | None = None) -> str:
    """Create and save a plot of Fourier candidate periods vs magnitudes.

    Highlights:
    - top_percent_points: the top-percent strongest maxima after suppression
    - high_snr_points: the strongest points that also clear the SNR filter
    - harmonic_points: points that passed the harmonic filter (multiples present)
    - median / SNR lines: horizontal reference lines for the current filter basis

    Returns the path to the saved PNG file.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except Exception:
        raise RuntimeError("matplotlib is required to generate plots. Install it in your environment.")

    if not out_path:
        from pathlib import Path
        import uuid
        out_dir = Path(__file__).parent / "static"
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        endpoint_slug = str(endpoint_id) if endpoint_id is not None else "unknown-endpoint"
        event_slug = str(native_event_id) if native_event_id is not None else "unknown-event"
        out_path = str(out_dir / f"fourier_{timestamp}_endpoint-{endpoint_slug}_event-{event_slug}_{uuid.uuid4().hex}.png")

    plt.figure(figsize=(10, 5))
    rounded_periods = [round(period) for period in periods]
    plt.plot(rounded_periods, magnitudes, label='Fourier magnitude')

    def _scatter(points, **kwargs):
        if points:
            xs = [round(p[0]) for p in points]
            ys = [p[1] for p in points]
            plt.scatter(xs, ys, **kwargs)

    _scatter(top_percent_points, facecolors='none', edgecolors='orange', marker='o', s=220, linewidths=2.4, label='top percentile after suppression')
    _scatter(high_snr_points, color='limegreen', marker='D', s=70, alpha=0.9, edgecolors='black', linewidths=0.6, label='high SNR points')
    _scatter(harmonic_points, color='purple', marker='*', s=140, alpha=0.9, edgecolors='black', linewidths=0.8, label='harmonic points')

    if median is not None:
        plt.axhline(median, color='slateblue', linestyle='-', linewidth=1.8, label='median')
    if median is not None and mad is not None and snr_threshold is not None:
        snr_delta = snr_threshold * mad
        plt.axhline(median + snr_delta, color='tomato', linestyle='--', linewidth=1.5, label=f'+{snr_threshold:g} SNR')
        plt.axhline(median - snr_delta, color='tomato', linestyle='--', linewidth=1.5, label=f'-{snr_threshold:g} SNR')

    ax = plt.gca()
    ax.ticklabel_format(axis='x', style='plain', useOffset=False)
    ax.xaxis.get_major_formatter().set_useOffset(False)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:.3f}"))

    plt.title('Fourier Magnitudes')
    plt.xlabel('Candidate period (milliseconds)')
    plt.ylabel('Magnitude')
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.show()
    plt.close()
    return out_path
