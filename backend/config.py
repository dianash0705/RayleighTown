from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "logs.db"


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


HARMONIC_ANALYSIS_CONFIG = HarmonicAnalysisConfig()

# Backward-compatible aliases for existing imports.
GHOST_PEAK_SUPPRESSION_ENABLED = HARMONIC_ANALYSIS_CONFIG.ghost_suppression_enabled
PHASE_GHOST_SUPPRESSION_ENABLED = HARMONIC_ANALYSIS_CONFIG.phase_ghost_suppression_enabled
PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD = HARMONIC_ANALYSIS_CONFIG.phase_similarity_threshold
