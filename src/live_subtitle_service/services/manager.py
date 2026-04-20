from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from uuid import uuid4

from live_subtitle_service.config import Settings
from live_subtitle_service.domain.models import StreamRequest, StreamStatus
from live_subtitle_service.services.runner import SubtitleSessionRunner
from live_subtitle_service.services.session import StreamSession

logger = logging.getLogger(__name__)


class StreamNotFoundError(Exception):
    pass


class StreamManager:
    def __init__(
        self,
        settings: Settings,
        runner_factory: Callable[[], SubtitleSessionRunner],
    ) -> None:
        self._settings = settings
        self._runner_factory = runner_factory
        self._sessions: dict[str, StreamSession] = {}
        self._external_index: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start_stream(self, request: StreamRequest) -> StreamSession:
        async with self._lock:
            if request.external_id:
                existing_id = self._external_index.get(request.external_id)
                existing = self._sessions.get(existing_id or "")
                if existing and existing.status in {StreamStatus.STARTING, StreamStatus.RUNNING}:
                    return existing
                if existing and existing.status not in {StreamStatus.STARTING, StreamStatus.RUNNING}:
                    self._sessions.pop(existing.id, None)

            session = StreamSession(
                stream_id=uuid4().hex,
                request=request,
                max_segments=self._settings.max_segments_per_stream,
                subscriber_queue_size=self._settings.subscriber_queue_size,
            )
            self._sessions[session.id] = session
            if request.external_id:
                self._external_index[request.external_id] = session.id

        task = asyncio.create_task(self._run_session(session), name=f"subtitle-stream-{session.id}")
        session.attach_task(task)
        return session

    async def list_streams(self) -> list[StreamSession]:
        async with self._lock:
            sessions = list(self._sessions.values())
        sessions = sorted(sessions, key=lambda session: session.created_at, reverse=True)
        seen_external: set[str] = set()
        deduped: list[StreamSession] = []
        for session in sessions:
            ext = session.request.external_id
            if ext:
                if ext in seen_external:
                    continue
                seen_external.add(ext)
            deduped.append(session)
        return deduped

    async def get_stream(self, stream_id: str) -> StreamSession:
        async with self._lock:
            session = self._sessions.get(stream_id)
        if session is None:
            raise StreamNotFoundError(stream_id)
        return session

    async def get_stream_by_external_id(self, external_id: str) -> StreamSession:
        async with self._lock:
            session_id = self._external_index.get(external_id)
            session = self._sessions.get(session_id or "")
            if session is None:
                session = next(
                    (
                        item
                        for item in self._sessions.values()
                        if item.request.external_id == external_id
                    ),
                    None,
                )

        if session is None:
            raise StreamNotFoundError(external_id)
        return session

    async def stop_stream(self, stream_id: str) -> StreamSession:
        session = await self.get_stream(stream_id)
        session.request_stop()
        await self._await_task(session.task)
        return session

    async def stop_stream_by_external_id(self, external_id: str) -> StreamSession:
        session = await self.get_stream_by_external_id(external_id)
        return await self.stop_stream(session.id)

    async def shutdown(self) -> None:
        sessions = await self.list_streams()
        await asyncio.gather(
            *[
                self.stop_stream(session.id)
                for session in sessions
                if session.status
                in {
                    StreamStatus.STARTING,
                    StreamStatus.RUNNING,
                    StreamStatus.STOPPING,
                }
            ],
            return_exceptions=True,
        )

    async def _run_session(self, session: StreamSession) -> None:
        runner = self._runner_factory()
        session.mark_running()

        try:
            await runner.run(session)
        except asyncio.CancelledError:
            session.request_stop()
            session.mark_stopped()
            raise
        except Exception as exc:
            logger.exception("Subtitle stream failed", extra={"stream_id": session.id})
            session.mark_failed(str(exc))
        else:
            if session.status != StreamStatus.FAILED:
                session.mark_stopped()

    async def _await_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return

        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._settings.shutdown_timeout_seconds,
            )
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
