from __future__ import annotations

from datetime import UTC, datetime, timedelta

from live_subtitle_service.api.schemas import SubtitleSegmentResponse
from live_subtitle_service.domain.models import SubtitleSegment


def test_subtitle_segment_response_includes_source_timing_and_processing_latency() -> None:
    session_started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    source_end_at = session_started_at + timedelta(milliseconds=4000)
    created_at = source_end_at + timedelta(milliseconds=1350)

    response = SubtitleSegmentResponse.from_segment(
        SubtitleSegment(
            stream_id="stream-1",
            sequence=7,
            start_ms=0,
            end_ms=4000,
            text="Merhaba dunya",
            raw_text="Merhaba dunya",
            created_at=created_at,
        ),
        session_started_at,
    )

    assert response.source_end_at == source_end_at
    assert response.processing_latency_ms == 1350
