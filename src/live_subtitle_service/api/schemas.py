from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from live_subtitle_service.config import Settings
from live_subtitle_service.domain.models import StreamRequest, StreamStatus, SubtitleSegment
from live_subtitle_service.services.session import StreamSession


class CreateStreamRequest(BaseModel):
    source_url: str = Field(..., description="Any ffmpeg-compatible live stream URL or input")
    external_id: str | None = Field(
        default=None,
        description="Optional caller-owned id. Active streams with the same id are reused.",
    )
    language: str | None = Field(
        default=None,
        description="Optional BCP-47-ish language hint. Omit to auto-detect and lock.",
    )
    model: str | None = Field(
        default=None,
        description="Local Whisper model size or local model path",
    )
    chunk_seconds: float | None = Field(default=None, gt=0)
    overlap_seconds: float | None = Field(default=None, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source_url cannot be blank")
        return cleaned

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_window(self) -> CreateStreamRequest:
        if self.chunk_seconds is not None and self.overlap_seconds is not None:
            if self.chunk_seconds <= self.overlap_seconds:
                raise ValueError("chunk_seconds must be greater than overlap_seconds")
        return self

    def to_domain(self, settings: Settings) -> StreamRequest:
        resolved_chunk_seconds = self.chunk_seconds or settings.chunk_seconds
        resolved_overlap_seconds = (
            self.overlap_seconds if self.overlap_seconds is not None else settings.overlap_seconds
        )
        if resolved_chunk_seconds <= resolved_overlap_seconds:
            raise ValueError("chunk_seconds must be greater than overlap_seconds")

        return StreamRequest(
            source_url=self.source_url,
            language=self.language,
            model=self.model or settings.default_transcription_model,
            chunk_seconds=resolved_chunk_seconds,
            overlap_seconds=resolved_overlap_seconds,
            external_id=self.external_id,
            metadata=self.metadata,
        )


class SubtitleSegmentResponse(BaseModel):
    stream_id: str
    sequence: int
    start_ms: int
    end_ms: int
    text: str
    raw_text: str
    created_at: datetime

    @classmethod
    def from_segment(cls, segment: SubtitleSegment) -> SubtitleSegmentResponse:
        return cls(
            stream_id=segment.stream_id,
            sequence=segment.sequence,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
            raw_text=segment.raw_text,
            created_at=segment.created_at,
        )


class StreamResponse(BaseModel):
    id: str
    external_id: str | None
    status: StreamStatus
    source_url: str
    language: str | None
    detected_language: str | None
    detected_language_probability: float | None
    model: str
    chunk_seconds: float
    overlap_seconds: float
    metadata: dict[str, str]
    created_at: datetime
    started_at: datetime | None
    stopped_at: datetime | None
    updated_at: datetime
    last_error: str | None
    segments_emitted: int
    dropped_chunks: int
    subscriber_count: int
    subtitles_path: str
    websocket_path: str
    subtitles_url: str
    websocket_url: str

    @classmethod
    def from_session(cls, session: StreamSession, settings: Settings) -> StreamResponse:
        subtitles_path = f"{settings.api_prefix}/streams/{session.id}/subtitles"
        websocket_path = f"{settings.api_prefix}/streams/{session.id}/ws"

        return cls(
            id=session.id,
            external_id=session.request.external_id,
            status=session.status,
            source_url=session.request.source_url,
            language=session.request.language,
            detected_language=session.detected_language,
            detected_language_probability=session.detected_language_probability,
            model=session.request.model,
            chunk_seconds=session.request.chunk_seconds,
            overlap_seconds=session.request.overlap_seconds,
            metadata=session.request.metadata,
            created_at=session.created_at,
            started_at=session.started_at,
            stopped_at=session.stopped_at,
            updated_at=session.updated_at,
            last_error=session.last_error,
            segments_emitted=session.segments_emitted,
            dropped_chunks=session.dropped_chunks,
            subscriber_count=session.subscriber_count,
            subtitles_path=subtitles_path,
            websocket_path=websocket_path,
            subtitles_url=_absolute_url(settings.public_http_base_url, subtitles_path),
            websocket_url=_absolute_websocket_url(
                settings.public_ws_base_url,
                settings.public_http_base_url,
                websocket_path,
            ),
        )


class SubtitleListResponse(BaseModel):
    stream_id: str
    items: list[SubtitleSegmentResponse]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


def _absolute_url(base_url: str | None, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _absolute_websocket_url(
    ws_base_url: str | None,
    http_base_url: str | None,
    path: str,
) -> str:
    if ws_base_url:
        return _absolute_url(ws_base_url, path)

    if http_base_url:
        base_url = http_base_url.rstrip("/")
        if base_url.startswith("https://"):
            return _absolute_url("wss://" + base_url.removeprefix("https://"), path)
        if base_url.startswith("http://"):
            return _absolute_url("ws://" + base_url.removeprefix("http://"), path)

    return path
