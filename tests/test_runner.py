from __future__ import annotations

from collections.abc import AsyncIterator

from live_subtitle_service.config import Settings
from live_subtitle_service.domain.models import AudioChunk, StreamRequest, TranscriptionResult
from live_subtitle_service.services.runner import SubtitleSessionRunner
from live_subtitle_service.services.session import StreamSession


class FakeAudioSource:
    def __init__(self, chunks: list[AudioChunk]) -> None:
        self._chunks = chunks

    async def iter_chunks(
        self,
        request: StreamRequest,
        stop_event,
    ) -> AsyncIterator[AudioChunk]:
        del request
        del stop_event
        for chunk in self._chunks:
            yield chunk


class FakeTranscriber:
    def __init__(self, transcripts: dict[int, TranscriptionResult]) -> None:
        self._transcripts = transcripts
        self.seen_languages: list[str | None] = []

    async def transcribe(
        self,
        chunk: AudioChunk,
        request: StreamRequest,
    ) -> TranscriptionResult:
        self.seen_languages.append(request.language)
        return self._transcripts[chunk.sequence]


async def test_runner_deduplicates_chunk_overlap() -> None:
    settings = Settings()
    request = StreamRequest(
        source_url="https://example.com/live.m3u8",
        language="tr",
        model="small",
        chunk_seconds=4.0,
        overlap_seconds=0.75,
    )
    session = StreamSession(
        stream_id="stream-1",
        request=request,
        max_segments=10,
        subscriber_queue_size=2,
    )
    runner = SubtitleSessionRunner(
        audio_source=FakeAudioSource(
            [
                AudioChunk(sequence=0, start_ms=0, end_ms=4000, pcm16=b"one"),
                AudioChunk(sequence=1, start_ms=3250, end_ms=7250, pcm16=b"two"),
            ]
        ),
        transcriber=FakeTranscriber(
            {
                0: TranscriptionResult(text="merhaba nasilsin"),
                1: TranscriptionResult(text="nasilsin bugun"),
            }
        ),
        settings=settings,
    )

    await runner.run(session)

    assert [segment.text for segment in session.recent_segments()] == [
        "merhaba nasilsin",
        "bugun",
    ]


async def test_runner_locks_detected_language_for_following_chunks() -> None:
    settings = Settings()
    request = StreamRequest(
        source_url="https://example.com/live.m3u8",
        language=None,
        model="tiny",
        chunk_seconds=4.0,
        overlap_seconds=0.75,
    )
    session = StreamSession(
        stream_id="stream-2",
        request=request,
        max_segments=10,
        subscriber_queue_size=2,
    )
    transcriber = FakeTranscriber(
        {
            0: TranscriptionResult(
                text="hello there",
                detected_language="en",
                detected_language_probability=0.95,
            ),
            1: TranscriptionResult(text="there again"),
        }
    )
    runner = SubtitleSessionRunner(
        audio_source=FakeAudioSource(
            [
                AudioChunk(sequence=0, start_ms=0, end_ms=4000, pcm16=b"one"),
                AudioChunk(sequence=1, start_ms=3250, end_ms=7250, pcm16=b"two"),
            ]
        ),
        transcriber=transcriber,
        settings=settings,
    )

    await runner.run(session)

    assert transcriber.seen_languages == [None, "en"]
    assert session.detected_language == "en"
    assert session.request.language == "en"
