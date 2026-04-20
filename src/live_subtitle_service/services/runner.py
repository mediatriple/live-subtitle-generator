from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Protocol

from live_subtitle_service.config import Settings
from live_subtitle_service.domain.models import (
    AudioChunk,
    StreamRequest,
    SubtitleSegment,
    TranscriptionResult,
)
from live_subtitle_service.services.session import StreamSession
from live_subtitle_service.utils.text import normalize_transcript_text, trim_overlapping_prefix


class AudioSource(Protocol):
    async def iter_chunks(
        self,
        request: StreamRequest,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[AudioChunk]: ...


class Transcriber(Protocol):
    async def transcribe(
        self,
        chunk: AudioChunk,
        request: StreamRequest,
    ) -> TranscriptionResult: ...


class SubtitleSessionRunner:
    def __init__(
        self,
        audio_source: AudioSource,
        transcriber: Transcriber,
        settings: Settings,
    ) -> None:
        self._audio_source = audio_source
        self._transcriber = transcriber
        self._settings = settings

    async def run(self, session: StreamSession) -> None:
        queue: asyncio.Queue[AudioChunk | None] = asyncio.Queue(
            maxsize=self._settings.chunk_queue_size
        )
        producer = asyncio.create_task(self._produce_chunks(session, queue))
        consumer = asyncio.create_task(self._consume_chunks(session, queue))

        try:
            await asyncio.gather(producer, consumer)
        except Exception:
            session.request_stop()
            producer.cancel()
            consumer.cancel()
            await asyncio.gather(producer, consumer, return_exceptions=True)
            raise

    async def _produce_chunks(
        self,
        session: StreamSession,
        queue: asyncio.Queue[AudioChunk | None],
    ) -> None:
        try:
            async for chunk in self._audio_source.iter_chunks(session.request, session.stop_event):
                if session.stop_event.is_set():
                    return

                if queue.full():
                    try:
                        queue.get_nowait()
                        session.increment_dropped_chunks()
                    except asyncio.QueueEmpty:
                        pass

                await queue.put(chunk)
        finally:
            while True:
                try:
                    queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()

    async def _consume_chunks(
        self,
        session: StreamSession,
        queue: asyncio.Queue[AudioChunk | None],
    ) -> None:
        last_raw_text = ""

        while True:
            chunk = await queue.get()
            if chunk is None:
                return

            result = await self._transcriber.transcribe(chunk, session.request)
            self._maybe_lock_language(session, result)
            raw_text = normalize_transcript_text(result.text)
            if not raw_text:
                continue

            text = trim_overlapping_prefix(last_raw_text, raw_text)
            last_raw_text = raw_text
            if not text:
                continue

            await session.publish_segment(
                SubtitleSegment(
                    stream_id=session.id,
                    sequence=chunk.sequence,
                    start_ms=chunk.start_ms,
                    end_ms=chunk.end_ms,
                    text=text,
                    raw_text=raw_text,
                )
            )

    def _maybe_lock_language(
        self,
        session: StreamSession,
        result: TranscriptionResult,
    ) -> None:
        if not result.detected_language:
            return

        session.register_detected_language(
            result.detected_language,
            result.detected_language_probability,
        )

        if session.request.language is not None:
            return

        probability = result.detected_language_probability
        if probability is None:
            return
        if probability < self._settings.transcription_language_lock_min_probability:
            return

        session.request.language = result.detected_language
