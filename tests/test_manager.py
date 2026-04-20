from __future__ import annotations

import asyncio

from live_subtitle_service.config import Settings
from live_subtitle_service.domain.models import StreamRequest, StreamStatus, SubtitleSegment
from live_subtitle_service.services.manager import StreamManager


class WaitingRunner:
    async def run(self, session) -> None:
        await session.publish_segment(
            SubtitleSegment(
                stream_id=session.id,
                sequence=0,
                start_ms=0,
                end_ms=4000,
                text="hello world",
                raw_text="hello world",
            )
        )
        await session.stop_event.wait()


async def test_manager_start_and_stop_stream() -> None:
    settings = Settings()
    manager = StreamManager(settings=settings, runner_factory=WaitingRunner)
    session = await manager.start_stream(
        StreamRequest(
            source_url="https://example.com/live.m3u8",
            language="en",
            model="small",
            chunk_seconds=4.0,
            overlap_seconds=0.75,
        )
    )

    await asyncio.sleep(0.05)
    running_session = await manager.get_stream(session.id)
    assert running_session.segments_emitted == 1

    stopped_session = await manager.stop_stream(session.id)
    assert stopped_session.status == StreamStatus.STOPPED


async def test_manager_reuses_active_external_stream() -> None:
    settings = Settings()
    manager = StreamManager(settings=settings, runner_factory=WaitingRunner)
    request = StreamRequest(
        source_url="https://example.com/live.m3u8",
        language="en",
        model="small",
        chunk_seconds=4.0,
        overlap_seconds=0.75,
        external_id="videoonly-v2:account-1:broadcast-1",
    )

    first = await manager.start_stream(request)
    second = await manager.start_stream(request)

    assert second.id == first.id
    assert await manager.get_stream_by_external_id(request.external_id) is first

    stopped = await manager.stop_stream_by_external_id(request.external_id)
    assert stopped.status == StreamStatus.STOPPED
