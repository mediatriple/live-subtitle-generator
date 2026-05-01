from __future__ import annotations

from datetime import UTC, datetime, timedelta

from live_subtitle_service.api.schemas import CreateStreamRequest, SubtitleSegmentResponse
from live_subtitle_service.config import Settings
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


def test_create_stream_request_preserves_model_for_broadcast_preview() -> None:
    request = CreateStreamRequest(
        source_url="https://example.com/live/broadcast_1.smil/playlist.m3u8",
        external_id="broadcast_1",
        language="tr",
        model="medium",
        metadata={"feature": "player-preview-live", "broadcast_uid": "broadcast_1"},
    )

    domain = request.to_domain(Settings(default_transcription_model="medium"))

    assert domain.language is None
    assert domain.model == "medium"


def test_create_stream_request_preserves_explicit_allowed_values_for_non_broadcast() -> None:
    request = CreateStreamRequest(
        source_url="https://example.com/live/custom.m3u8",
        external_id="custom-stream",
        language="tr",
        model="medium",
    )

    domain = request.to_domain(Settings(default_transcription_model="small"))

    assert domain.language == "tr"
    assert domain.model == "medium"


def test_create_stream_request_preserves_admin_model_for_broadcast_live_crop() -> None:
    request = CreateStreamRequest(
        source_url="https://example.com/live/broadcast_1.smil/playlist.m3u8",
        external_id="broadcast_1",
        language="tr",
        model="medium",
        metadata={"feature": "live-crop", "broadcast_uid": "broadcast_1"},
    )

    domain = request.to_domain(Settings(default_transcription_model="medium"))

    assert domain.language == "tr"
    assert domain.model == "medium"


def test_create_stream_request_preserves_large_v3_model() -> None:
    request = CreateStreamRequest(
        source_url="https://example.com/live/custom.m3u8",
        external_id="custom-stream",
        language="tr",
        model="large-v3",
    )

    domain = request.to_domain(Settings(default_transcription_model="large-v3"))

    assert domain.model == "large-v3"
