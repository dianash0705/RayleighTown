"""Shared types for incremental log ingest and analysis scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NativeEventImpact:
    native_event_id: int
    new_min_ms: int
    new_max_ms: int
    new_event_count: int = 0
    series_key: str = ""

    @property
    def impact_key(self) -> tuple[int, str]:
        return (self.native_event_id, self.series_key)

    def merge(self, other: NativeEventImpact) -> None:
        if self.impact_key != other.impact_key:
            raise ValueError("Cannot merge impacts for different event series.")
        self.new_min_ms = min(self.new_min_ms, other.new_min_ms)
        self.new_max_ms = max(self.new_max_ms, other.new_max_ms)
        self.new_event_count += other.new_event_count


@dataclass
class InsertEventsResult:
    inserted_count: int
    skipped_count: int
    impacts: list[NativeEventImpact] = field(default_factory=list)

    @property
    def has_new_events(self) -> bool:
        return self.inserted_count > 0


@dataclass
class IncrementalAnalysisResult:
    endpoint_id: str
    method: str
    event_types_analyzed: list[int]
    new_events_queued: int
    total_events_loaded: int
    affected_type_events_loaded: int
    preserved_windows_kept: int
    alert_windows_written: int
    alert_groups_total: int
    elapsed_sec: float
