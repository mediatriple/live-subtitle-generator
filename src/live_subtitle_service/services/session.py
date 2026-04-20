from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime

from live_subtitle_service.domain.models import (
    StreamRequest,
    StreamStatus,
    SubtitleSegment,
    utc_now,
)


class StreamSession:
    def __init__(
        self,
        stream_id: str,
        request: StreamRequest,
        max_segments: int,
        subscriber_queue_size: int,
    ) -> None:
        self.id = stream_id
        self.request = request
        self.status = StreamStatus.STARTING
        self.created_at = utc_now()
        self.updated_at = self.created_at
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.last_error: str | None = None
        self.detected_language: str | None = None
        self.detected_language_probability: float | None = None
        self.segments_emitted = 0
        self.dropped_chunks = 0
        self.stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._segments: deque[SubtitleSegment] = deque(maxlen=max_segments)
        self._subscribers: set[asyncio.Queue[SubtitleSegment]] = set()
        self._subscriber_queue_size = subscriber_queue_size

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def attach_task(self, task: asyncio.Task[None]) -> None:
        self._task = task

    def mark_running(self) -> None:
        self.status = StreamStatus.RUNNING
        self.started_at = utc_now()
        self.updated_at = self.started_at

    def request_stop(self) -> None:
        if self.status in {StreamStatus.STOPPED, StreamStatus.FAILED}:
            return
        self.status = StreamStatus.STOPPING
        self.updated_at = utc_now()
        self.stop_event.set()

    def mark_stopped(self) -> None:
        self.status = StreamStatus.STOPPED
        self.stopped_at = utc_now()
        self.updated_at = self.stopped_at
        self.stop_event.set()

    def mark_failed(self, message: str) -> None:
        self.status = StreamStatus.FAILED
        self.last_error = message
        self.stopped_at = utc_now()
        self.updated_at = self.stopped_at
        self.stop_event.set()

    def increment_dropped_chunks(self) -> None:
        self.dropped_chunks += 1
        self.updated_at = utc_now()

    def register_detected_language(
        self,
        language: str,
        probability: float | None,
    ) -> None:
        self.detected_language = language
        self.detected_language_probability = probability
        self.updated_at = utc_now()

    def recent_segments(self, limit: int | None = None) -> list[SubtitleSegment]:
        if limit is None:
            return list(self._segments)
        return list(self._segments)[-limit:]

    async def publish_segment(self, segment: SubtitleSegment) -> None:
        self._segments.append(segment)
        self.segments_emitted += 1
        self.updated_at = utc_now()

        stale_queues: list[asyncio.Queue[SubtitleSegment]] = []
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    stale_queues.append(queue)
                    continue

            try:
                queue.put_nowait(segment)
            except asyncio.QueueFull:
                stale_queues.append(queue)

        for queue in stale_queues:
            self._subscribers.discard(queue)

    def subscribe(self) -> asyncio.Queue[SubtitleSegment]:
        queue: asyncio.Queue[SubtitleSegment] = asyncio.Queue(maxsize=self._subscriber_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[SubtitleSegment]) -> None:
        self._subscribers.discard(queue)
