"""Synthetic timestamp builders for Fourier / harmonic analysis tests."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class NoiseProfile:
    """Describe optional noise layers mixed into synthetic event streams."""

    uniform_event_count: int = 0
    uniform_range_ms: tuple[int, int] = (0, 24 * 60 * 60_000)
    jitter_ms: int = 0
    secondary_periods_ms: tuple[int, ...] = ()
    secondary_jitter_ms: int = 0
    seed: int = 7


def make_periodic_timestamps_ms(
    period_ms: int,
    count: int,
    *,
    start_ms: int = 0,
    phase_ms: int = 0,
) -> list[float]:
    return [float(start_ms + phase_ms + index * period_ms) for index in range(count)]


def add_uniform_random_events(
    timestamps: list[float],
    *,
    event_count: int,
    range_ms: tuple[int, int],
    seed: int = 7,
) -> list[float]:
    if event_count <= 0:
        return list(timestamps)

    rng = random.Random(seed)
    start_ms, end_ms = range_ms
    noisy = list(timestamps)
    noisy.extend(float(rng.randint(start_ms, end_ms)) for _ in range(event_count))
    noisy.sort()
    return noisy


def add_gaussian_jitter(
    timestamps: Iterable[float],
    *,
    jitter_ms: int,
    seed: int = 7,
) -> list[float]:
    if jitter_ms <= 0:
        return [float(timestamp) for timestamp in timestamps]

    rng = random.Random(seed)
    jittered = []
    for timestamp in timestamps:
        offset = int(round(rng.gauss(0, jitter_ms / 3)))
        offset = max(-jitter_ms, min(jitter_ms, offset))
        jittered.append(float(timestamp + offset))
    jittered.sort()
    return jittered


def _append_secondary_periods(
    timestamps: list[float],
    *,
    periods_ms: tuple[int, ...],
    range_ms: tuple[int, int],
    jitter_ms: int,
    seed: int,
) -> list[float]:
    if not periods_ms:
        return timestamps

    rng = random.Random(seed)
    start_ms, end_ms = range_ms
    extended = list(timestamps)

    for period_ms in periods_ms:
        base_ts = start_ms
        while base_ts <= end_ms:
            offset = 0
            if jitter_ms > 0:
                offset = int(round(rng.gauss(0, jitter_ms / 3)))
                offset = max(-jitter_ms, min(jitter_ms, offset))
            extended.append(float(base_ts + offset))
            base_ts += period_ms

    extended.sort()
    return extended


def make_mixed_timestamps_ms(
    primary_period_ms: int,
    primary_count: int,
    *,
    start_ms: int = 0,
    phase_ms: int = 0,
    noise: NoiseProfile | None = None,
) -> list[float]:
    """Build a periodic stream and optionally layer uniform / jitter / secondary noise."""
    timestamps = make_periodic_timestamps_ms(
        primary_period_ms,
        primary_count,
        start_ms=start_ms,
        phase_ms=phase_ms,
    )

    if noise is None:
        return timestamps

    if noise.jitter_ms > 0:
        timestamps = add_gaussian_jitter(
            timestamps,
            jitter_ms=noise.jitter_ms,
            seed=noise.seed,
        )

    timestamps = _append_secondary_periods(
        timestamps,
        periods_ms=noise.secondary_periods_ms,
        range_ms=noise.uniform_range_ms,
        jitter_ms=noise.secondary_jitter_ms,
        seed=noise.seed + 1,
    )

    timestamps = add_uniform_random_events(
        timestamps,
        event_count=noise.uniform_event_count,
        range_ms=noise.uniform_range_ms,
        seed=noise.seed + 2,
    )
    return timestamps
