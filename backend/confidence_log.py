"""Append-only JSONL logging for confidence scoring breakdowns."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CONFIDENCE_LOG_DIR

logger = logging.getLogger(__name__)


def _log_file_path() -> Path:
    day_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return CONFIDENCE_LOG_DIR / f"confidence_{day_stamp}.jsonl"


def log_confidence_event(event_type: str, payload: dict[str, Any]) -> None:
    """
    Append one confidence event to the daily JSONL log.

    ``event_type`` examples: ``window_detection``, ``group_merge``, ``final_alert``.
    """
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **payload,
    }

    try:
        CONFIDENCE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _log_file_path().open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
            log_file.write("\n")
    except OSError as error:
        logger.warning("Failed to write confidence log: %s", error)

    logger.debug("confidence.%s %s", event_type, json.dumps(payload, sort_keys=True))
