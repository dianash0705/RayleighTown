from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "logs.db"

# Auth / session settings.
# The Flask session secret is generated on first run and persisted here so that
# existing logins keep working across server restarts.
SECRET_KEY_PATH = DB_DIR / "secret_key"

# When True, log uploads must carry a valid endpointID + endpointSecret pair that
# matches an endpoint registered by an admin. Turn off only for local testing.
REQUIRE_ENDPOINT_AUTH = True


@dataclass(frozen=True)
class CandidatePeriodGroupConfig:
	name: str
	window_size_ms: int
	start_ms: int
	end_ms: int
	step_ms: int


CANDIDATE_PERIOD_GROUP_CONFIGS: tuple[CandidatePeriodGroupConfig, ...] = (
	CandidatePeriodGroupConfig(
		name="short",
		window_size_ms=15 * 60_000,
		start_ms=1_500,
		end_ms=60_000,
		step_ms=500,
	),
	CandidatePeriodGroupConfig(
		name="medium",
		window_size_ms=6 * 60 * 60_000,
		start_ms=90_000,
		end_ms=1_800_000,
		step_ms=30_000,
	),
	CandidatePeriodGroupConfig(
		name="long",
		window_size_ms=24 * 60 * 60_000,
		start_ms=35 * 60_000,
		end_ms=120 * 60_000,
		step_ms=5 * 60_000,
	),
	CandidatePeriodGroupConfig(
		name="very_long",
		window_size_ms=7 * 24 * 60 * 60_000,
		start_ms=int(2.5 * 60 * 60_000),
		end_ms=24 * 60 * 60_000,
		step_ms=30 * 60_000,
	),
)

@dataclass(frozen=True)
class HarmonicAnalysisConfig:
	"""Tunable settings for harmonic support checks and ghost-peak suppression."""

	# Harmonic support filter (peak validation)
	# harmony_peak_count controls how many divisors are checked via range(2, count + 2):
	#   1 -> only period/2
	#   2 -> period/2 and period/3
	#   3 -> period/2, period/3, and period/4
	# (Same default as the former HARMONY_PEAK_COUNT = 2 in brain.py.)
	harmony_peak_count: int = 1
	harmony_magnitude_threshold: float = 0.8
	# When True, evaluate_period() is used for harmonics missing from the sampled grid.
	use_dynamic_harmonic_eval: bool = True
	use_phase_in_harmonic_check: bool = False

	# Fourier peak-selection pipeline
	snr_threshold: float = 3.0
	top_percent: float = 0.10
	suppression_radius_ms: float = 500.0
	window_overlap_ratio: float = 0.5
	min_events_for_alert: int = 4

	# Post-alert ghost peak suppression (long period -> short period)
	ghost_suppression_enabled: bool = True
	phase_ghost_suppression_enabled: bool = False
	phase_similarity_threshold: float = 0.9
	harmonic_tolerance_ratio: float = 0.05

	# Prefer half-period (e.g. 60s) over 2x alias (120s) only when the longer period
	# shows a cancellation signature — weak 2x magnitude vs strong half-period.
	# Does not walk further down (/3, /4) because shorter periods are ambiguous harmonics.
	superharmonic_canonicalization_enabled: bool = True
	superharmonic_half_strength_ratio: float = 1.25
	superharmonic_weak_double_ratio: float = 0.55

	# Prefer the longest strong harmonic multiple (e.g. 5 min over 1 min alias).
	# Disabled by default: stepping up can suppress genuine shorter periods when
	# longer harmonics are accidentally strengthened by noise or overlapping signals.
	subharmonic_alias_resolution_enabled: bool = False
	subharmonic_alias_min_magnitude_ratio: float = 0.92
	subharmonic_alias_max_multiplier: int = 30
	# Reject detections that collapse to one instant (not enough distinct timestamps).
	min_unique_timestamps_for_period: int = 3
	# Soft confidence penalty when period is much shorter than median event spacing.
	min_period_to_median_gap_ratio: float = 0.45
	max_spacing_alias_penalty: float = 22.0


HARMONIC_ANALYSIS_CONFIG = HarmonicAnalysisConfig()


@dataclass(frozen=True)
class ConfidenceScoringConfig:
	"""
	Tunable weights for multi-factor confidence (see ``confidence_scoring.py``).

	Window layer caps sum to ``max_window_score``; group bonuses can raise the
	final merged alert up to 100.
	"""

	# Window layer
	base_peak_scale: float = 70.0
	max_base_peak: float = 70.0
	max_snr_bonus: float = 15.0
	snr_decay: float = 2.0
	max_harmonic_bonus: float = 12.0
	max_phase_bonus: float = 12.0
	max_window_score: float = 85.0
	# Cap harmonic+phase unless base_peak is very strong (all-stars alignment).
	shape_bonus_to_base_cap_ratio: float = 0.50
	all_stars_min_base_peak: float = 48.0

	# Divisors used only for confidence (filters still check divisor 2 only).
	confidence_harmonic_divisors: tuple[int, ...] = (2, 3, 4, 5, 6)
	harmonic_weights: dict[int, float] = field(
		default_factory=lambda: {3: 0.30, 4: 0.25, 5: 0.25, 6: 0.20}
	)
	phase_weights: dict[int, float] = field(
		default_factory=lambda: {2: 0.30, 3: 0.25, 4: 0.20, 5: 0.15, 6: 0.10}
	)

	# Group layer (merged overlapping windows)
	max_count_bonus: float = 20.0
	count_bonus_scale: float = 4.0
	count_bonus_phase_gate: float = 0.40
	max_streak_bonus: float = 15.0
	streak_bonus_scale: float = 5.0
	streak_min_windows: int = 3
	streak_step_tolerance_ratio: float = 0.15
	max_phase_consistency_bonus: float = 10.0
	max_phase_penalty: float = 22.0
	phase_penalty_threshold: float = 0.45
	# Lone window detections without merge corroboration — modest cap when base is
	# not clearly dominant (reduces pure-noise single-window false highs).
	uncorroborated_single_window_cap: float = 62.0
	uncorroborated_single_window_base_threshold: float = 84.0
	single_window_confidence_penalty: float = 10.0

	# Logging
	confidence_logging_enabled: bool = True


CONFIDENCE_SCORING_CONFIG = ConfidenceScoringConfig()
CONFIDENCE_LOG_DIR = BASE_DIR / "logs" / "confidence"
BENCHMARK_LOGS_CONFIG_PATH = BASE_DIR / "benchmark_logs.json"
BENCHMARK_EXPECTATIONS_PATH = BASE_DIR / "benchmark_expectations.yaml"


@dataclass(frozen=True)
class EventMatchingConfig:
	"""Settings for assigning log events to periodic window alerts."""

	# Gaussian jitter tolerance when scoring distance to the nearest expected tick.
	jitter_sigma_ms: float = 500.0
	# Minimum per-event match confidence (0-100) stored in eventAlertMap.
	min_match_confidence: int = 25


EVENT_MATCHING_CONFIG = EventMatchingConfig()

# Backward-compatible aliases for existing imports.
GHOST_PEAK_SUPPRESSION_ENABLED = HARMONIC_ANALYSIS_CONFIG.ghost_suppression_enabled
PHASE_GHOST_SUPPRESSION_ENABLED = HARMONIC_ANALYSIS_CONFIG.phase_ghost_suppression_enabled
PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD = HARMONIC_ANALYSIS_CONFIG.phase_similarity_threshold
