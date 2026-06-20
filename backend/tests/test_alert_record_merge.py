import pytest

from brain import AlertRecord, _merge_overlapping_alert_records


def _record(
    *,
    ts_begin: int,
    ts_end: int,
    period_ts: float,
    confidence: int,
) -> AlertRecord:
    return AlertRecord(
        endpoint_id="ep-1",
        native_event_id=4624,
        matched_events=(),
        ts_begin=ts_begin,
        ts_end=ts_end,
        period_ts=period_ts,
        confidence=confidence,
    )


@pytest.mark.unit
class TestMergeOverlappingAlertRecords:
    def test_subsumed_weak_window_is_dropped(self):
        strong = _record(ts_begin=0, ts_end=50_000, period_ts=31_000.0, confidence=100)
        weak = _record(ts_begin=30_000, ts_end=35_000, period_ts=33_000.0, confidence=40)

        merged = _merge_overlapping_alert_records([strong, weak])

        assert merged == [strong]

    def test_overlapping_same_period_windows_merge_to_wider_span(self):
        left = _record(ts_begin=0, ts_end=40_000, period_ts=31_000.0, confidence=90)
        right = _record(ts_begin=30_000, ts_end=55_000, period_ts=31_500.0, confidence=85)

        merged = _merge_overlapping_alert_records([left, right])

        assert len(merged) == 1
        assert merged[0].ts_begin == 0
        assert merged[0].ts_end == 55_000
        assert merged[0].confidence == 90

    def test_non_overlapping_windows_are_kept(self):
        first = _record(ts_begin=0, ts_end=10_000, period_ts=31_000.0, confidence=80)
        second = _record(ts_begin=20_000, ts_end=30_000, period_ts=31_000.0, confidence=70)

        merged = _merge_overlapping_alert_records([first, second])

        assert merged == [first, second]
