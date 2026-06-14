"""Background analysis queue: coalesce per-endpoint jobs and run incremental brain passes."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from config import INGESTION_CONFIG
from ingestion_models import IncrementalAnalysisResult, NativeEventImpact

logger = logging.getLogger(__name__)

QueueAction = Literal["created", "merged"]


@dataclass
class EndpointAnalysisJob:
    endpoint_id: str
    impacts: dict[int, NativeEventImpact] = field(default_factory=dict)
    method: str = "fourier"

    @property
    def impact_list(self) -> list[NativeEventImpact]:
        return list(self.impacts.values())

    @property
    def new_event_count(self) -> int:
        return sum(impact.new_event_count for impact in self.impacts.values())

    @property
    def event_type_ids(self) -> list[int]:
        return sorted(self.impacts)

    def merge_impacts(self, impacts: list[NativeEventImpact]) -> None:
        for impact in impacts:
            existing = self.impacts.get(impact.native_event_id)
            if existing is None:
                self.impacts[impact.native_event_id] = NativeEventImpact(
                    native_event_id=impact.native_event_id,
                    new_min_ms=impact.new_min_ms,
                    new_max_ms=impact.new_max_ms,
                    new_event_count=impact.new_event_count,
                )
            else:
                existing.merge(impact)


def _format_analysis_result(result: IncrementalAnalysisResult) -> str:
    return (
        f"endpoint={result.endpoint_id} method={result.method} "
        f"eventTypes={result.event_types_analyzed} "
        f"newEvents={result.new_events_queued} "
        f"loadedEvents={result.total_events_loaded} "
        f"affectedTypeEvents={result.affected_type_events_loaded} "
        f"preservedWindows={result.preserved_windows_kept} "
        f"windowsWritten={result.alert_windows_written} "
        f"alertGroups={result.alert_groups_total} "
        f"elapsed={result.elapsed_sec:.2f}s"
    )


class AnalysisQueue:
    def __init__(self, poll_interval_sec: float | None = None):
        self._poll_interval_sec = (
            INGESTION_CONFIG.analysis_poll_interval_sec
            if poll_interval_sec is None
            else poll_interval_sec
        )
        self._lock = threading.Lock()
        self._pending: dict[str, EndpointAnalysisJob] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(
        self,
        endpoint_id: str,
        impacts: list[NativeEventImpact],
        *,
        method: str = "fourier",
    ) -> QueueAction | None:
        if not impacts:
            return None

        with self._lock:
            job = self._pending.get(endpoint_id)
            if job is None:
                job = EndpointAnalysisJob(endpoint_id=endpoint_id, method=method)
                self._pending[endpoint_id] = job
                action: QueueAction = "created"
            else:
                action = "merged"
            job.merge_impacts(impacts)
            if method:
                job.method = method
            pending_count = len(self._pending)
        self._wake.set()

        logger.info(
            "Analysis queued %s action=%s eventTypes=%s newEvents=%s pendingEndpoints=%s",
            endpoint_id,
            action,
            [impact.native_event_id for impact in impacts],
            sum(impact.new_event_count for impact in impacts),
            pending_count,
        )
        return action

    def _drain(self) -> list[EndpointAnalysisJob]:
        with self._lock:
            jobs = list(self._pending.values())
            self._pending.clear()
        return jobs

    def _run_job(self, job: EndpointAnalysisJob) -> None:
        from database import incremental_recompute_alerts_for_endpoint

        logger.info(
            "Brain started endpoint=%s method=%s eventTypes=%s newEvents=%s",
            job.endpoint_id,
            job.method,
            job.event_type_ids,
            job.new_event_count,
        )
        started = time.monotonic()
        try:
            result = incremental_recompute_alerts_for_endpoint(
                job.endpoint_id,
                job.impact_list,
                method=job.method,
            )
            logger.info("Brain finished %s", _format_analysis_result(result))
        except Exception:
            elapsed = time.monotonic() - started
            logger.exception(
                "Brain failed endpoint=%s method=%s eventTypes=%s newEvents=%s elapsed=%.2fs",
                job.endpoint_id,
                job.method,
                job.event_type_ids,
                job.new_event_count,
                elapsed,
            )

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self._poll_interval_sec)
            self._wake.clear()
            if self._stop.is_set():
                break

            jobs = self._drain()
            if not jobs:
                continue

            logger.info("Analysis worker processing %s queued endpoint(s)", len(jobs))
            for job in jobs:
                self._run_job(job)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="analysis-queue-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Analysis worker started (poll every %.1fs)",
            self._poll_interval_sec,
        )

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def flush_sync(self) -> None:
        """Process all pending jobs on the calling thread (for tests)."""
        jobs = self._drain()
        for job in jobs:
            self._run_job(job)


_analysis_queue: AnalysisQueue | None = None


def get_analysis_queue() -> AnalysisQueue:
    global _analysis_queue
    if _analysis_queue is None:
        _analysis_queue = AnalysisQueue()
    return _analysis_queue


def start_analysis_worker() -> None:
    if INGESTION_CONFIG.analysis_worker_enabled:
        get_analysis_queue().start()


def stop_analysis_worker() -> None:
    if _analysis_queue is not None:
        _analysis_queue.stop()
