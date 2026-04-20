from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class StreamStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(slots=True)
class StreamRequest:
    source_url: str
    language: str | None
    model: str
    chunk_seconds: float
    overlap_seconds: float
    external_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AudioChunk:
    sequence: int
    start_ms: int
    end_ms: int
    pcm16: bytes


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    detected_language: str | None = None
    detected_language_probability: float | None = None


@dataclass(slots=True)
class SubtitleSegment:
    stream_id: str
    sequence: int
    start_ms: int
    end_ms: int
    text: str
    raw_text: str
    created_at: datetime = field(default_factory=utc_now)
