"""Background ingest + analysis queue for async log processing."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from config import INGESTION_CONFIG
from ingestion_models import IncrementalAnalysisResult, NativeEventImpact
from log_registry import LOG_TYPE_CONFIG

logger = logging.getLogger(__name__)

QueueAction = Literal["created", "merged"]


@dataclass
class IngestJob:
    endpoint_id: str
    log_id: int
    saved_path: str
    filename: str
    hostname: str | None = None
    ip: str | None = None


@dataclass
class EndpointAnalysisJob:
    endpoint_id: str
    impacts: dict[tuple[int, str], NativeEventImpact] = field(default_factory=dict)
    method: str = "fourier"

    @property
    def impact_list(self) -> list[NativeEventImpact]:
        return list(self.impacts.values())

    @property
    def new_event_count(self) -> int:
        return sum(impact.new_event_count for impact in self.impacts.values())

    @property
    def event_type_ids(self) -> list[int]:
        return sorted({native_event_id for native_event_id, _ in self.impacts})

    def merge_impacts(self, impacts: list[NativeEventImpact]) -> None:
        for impact in impacts:
            existing = self.impacts.get(impact.impact_key)
            if existing is None:
                self.impacts[impact.impact_key] = NativeEventImpact(
                    native_event_id=impact.native_event_id,
                    series_key=impact.series_key,
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
        self._pending_ingest: list[IngestJob] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue_ingest(self, job: IngestJob) -> None:
        with self._lock:
            self._pending_ingest.append(job)
            pending_ingest = len(self._pending_ingest)
        self._wake.set()
        logger.info(
            "Ingest queued endpoint=%s logID=%s file=%s pendingIngest=%s",
            job.endpoint_id,
            job.log_id,
            job.filename,
            pending_ingest,
        )

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

    def _drain_ingest(self) -> list[IngestJob]:
        with self._lock:
            jobs = list(self._pending_ingest)
            self._pending_ingest.clear()
        return jobs

    def _drain(self) -> list[EndpointAnalysisJob]:
        with self._lock:
            jobs = list(self._pending.values())
            self._pending.clear()
        return jobs

    def _run_ingest_job(self, job: IngestJob) -> None:
        from database import insert_events, upsert_endpoint

        log_config = LOG_TYPE_CONFIG.get(job.log_id)
        if log_config is None:
            logger.error(
                "Ingest failed endpoint=%s unsupported logID=%s file=%s",
                job.endpoint_id,
                job.log_id,
                job.filename,
            )
            return

        saved_path = Path(job.saved_path)
        if not saved_path.exists():
            logger.error(
                "Ingest failed endpoint=%s missing file=%s",
                job.endpoint_id,
                job.saved_path,
            )
            return

        started = time.monotonic()
        file_size_mb = saved_path.stat().st_size / (1024 * 1024)
        logger.info(
            "Ingest started endpoint=%s logID=%s file=%s size=%.1fMB",
            job.endpoint_id,
            job.log_id,
            job.filename,
            file_size_mb,
        )

        try:
            extractor = log_config["extractor"]
            event_id_whitelist = log_config["event_id_whitelist"]
            whitelisted_events = extractor(saved_path, event_id_whitelist, job.log_id)
            inserted_result = insert_events(job.endpoint_id, job.log_id, whitelisted_events)
            upsert_endpoint(
                job.endpoint_id,
                job.hostname,
                job.ip,
                int(datetime.now(timezone.utc).timestamp() * 1000),
            )

            analysis_action = None
            if inserted_result.has_new_events:
                analysis_action = self.enqueue(job.endpoint_id, inserted_result.impacts)

            logger.info(
                "Ingest finished endpoint=%s logID=%s extracted=%s inserted=%s skipped=%s "
                "analysisQueued=%s queueAction=%s elapsed=%.2fs",
                job.endpoint_id,
                job.log_id,
                len(whitelisted_events),
                inserted_result.inserted_count,
                inserted_result.skipped_count,
                inserted_result.has_new_events,
                analysis_action,
                time.monotonic() - started,
            )
        except Exception:
            logger.exception(
                "Ingest failed endpoint=%s logID=%s file=%s elapsed=%.2fs",
                job.endpoint_id,
                job.log_id,
                job.filename,
                time.monotonic() - started,
            )

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

            ingest_jobs = self._drain_ingest()
            if ingest_jobs:
                logger.info("Background worker processing %s ingest job(s)", len(ingest_jobs))
                for job in ingest_jobs:
                    self._run_ingest_job(job)

            jobs = self._drain()
            if not jobs:
                continue

            logger.info("Background worker processing %s analysis job(s)", len(jobs))
            for job in jobs:
                self._run_job(job)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="background-ingest-analysis-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Background worker started (poll every %.1fs)",
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
        for job in self._drain_ingest():
            self._run_ingest_job(job)
        for job in self._drain():
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
