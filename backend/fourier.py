import numpy as np
import math
import random

try:
    from tqdm.auto import tqdm
except ImportError:  # Keep function usable even if tqdm is not installed.
    def tqdm(iterable, **kwargs):
        return iterable

RADIUS = 15_000
PERCENTILE = 0.10
SMALLEST_PERIOD_MS = 1_000 # in milliseconds, needs to be greater than the expected JITTER
SMALLEST_APPEARANCE_COUNT = 7 # idk once a week
NUMBER_OF_DIFFERENT_PERIODS = 300

RANDOM_SEED = 7
NOISE_EVENT_COUNT = 220
TIME_RANGE_MS = (0, 24 * 60 * 60 * 1000)  # 24 hours in milliseconds
PRIMARY_PERIOD_MS = 3_600_000
SECONDARY_PERIOD_MS = 1_080_000
REPEATING_SERIES_PERIOD_MS = 900_000
REPEATING_SERIES_PHASE_MS = 120_000
MAX_JITTER_MS = 10_000

def get_candidate_periods_ms(time_range_ms: float) -> list[float]:
    largest_period_ms = time_range_ms / SMALLEST_APPEARANCE_COUNT
    if largest_period_ms <= SMALLEST_PERIOD_MS:
        raise ValueError("time range is too small to build candidate periods")

    difference = (largest_period_ms - SMALLEST_PERIOD_MS) / NUMBER_OF_DIFFERENT_PERIODS
    periods_ms = [
        (SMALLEST_PERIOD_MS + (n * difference))
        for n in range(NUMBER_OF_DIFFERENT_PERIODS)
    ]
    return periods_ms

def fourier_transform(timestamps: list[float], show_progress: bool = False):
    if len(timestamps) < 2:
        raise ValueError("timestamps must contain at least two values")

    time_range_ms = timestamps[-1] - timestamps[0]
    periods_ms = get_candidate_periods_ms(time_range_ms)

    point_xs = []
    point_ys = []
    period_iterator = tqdm(
        periods_ms,
        desc="Fourier transform",
        unit="period",
        disable=not show_progress,
    )
    for period_ms in period_iterator:
        sum_x = 0
        sum_y = 0
        for time_of_event in timestamps:
            alpha = (time_of_event % period_ms) / period_ms * (2 * math.pi)
            sum_x += math.cos(alpha)
            sum_y += math.sin(alpha)

        avg_x = sum_x / len(timestamps)
        avg_y = sum_y / len(timestamps)

        distance = math.sqrt(avg_y**2 + avg_x**2)
        point_xs.append(period_ms)
        point_ys.append(distance)
    
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
