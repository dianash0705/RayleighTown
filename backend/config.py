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
		start_ms=2_000,
		end_ms=60_000,
		step_ms=1_000,
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

GHOST_PEAK_SUPPRESSION_ENABLED = True
PHASE_GHOST_SUPPRESSION_ENABLED = False
PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD = 0.9
