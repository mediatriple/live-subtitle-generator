from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from live_subtitle_service.config import Settings
from live_subtitle_service.domain.models import AudioChunk, StreamRequest
from live_subtitle_service.utils.audio import bytes_to_milliseconds

logger = logging.getLogger(__name__)


class FFmpegPCMChunkSource:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def iter_chunks(
        self,
        request: StreamRequest,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[AudioChunk]:
        process = await asyncio.create_subprocess_exec(
            *self._build_command(request.source_url),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(self._drain_stderr(process))

        bytes_per_second = self._settings.sample_rate * self._settings.channels * 2
        chunk_bytes = int(request.chunk_seconds * bytes_per_second)
        stride_bytes = int((request.chunk_seconds - request.overlap_seconds) * bytes_per_second)
        read_size = max(bytes_per_second // 2, 4096)
        buffer = bytearray()
        offset_bytes = 0
        sequence = 0

        try:
            if process.stdout is None:
                raise RuntimeError("ffmpeg stdout pipe was not created")

            while not stop_event.is_set():
                data = await process.stdout.read(read_size)
                if not data:
                    break

                buffer.extend(data)
                while len(buffer) >= chunk_bytes and not stop_event.is_set():
                    end_bytes = offset_bytes + chunk_bytes
                    yield AudioChunk(
                        sequence=sequence,
                        start_ms=bytes_to_milliseconds(
                            offset_bytes,
                            self._settings.sample_rate,
                            self._settings.channels,
                        ),
                        end_ms=bytes_to_milliseconds(
                            end_bytes,
                            self._settings.sample_rate,
                            self._settings.channels,
                        ),
                        pcm16=bytes(buffer[:chunk_bytes]),
                    )
                    del buffer[:stride_bytes]
                    offset_bytes += stride_bytes
                    sequence += 1

            if not stop_event.is_set() and len(buffer) >= max(chunk_bytes // 2, bytes_per_second):
                end_bytes = offset_bytes + len(buffer)
                yield AudioChunk(
                    sequence=sequence,
                    start_ms=bytes_to_milliseconds(
                        offset_bytes,
                        self._settings.sample_rate,
                        self._settings.channels,
                    ),
                    end_ms=bytes_to_milliseconds(
                        end_bytes,
                        self._settings.sample_rate,
                        self._settings.channels,
                    ),
                    pcm16=bytes(buffer),
                )

            if not stop_event.is_set():
                await process.wait()
                if process.returncode not in {0, None}:
                    raise RuntimeError(f"ffmpeg exited with code {process.returncode}")
        finally:
            await self._terminate_process(process)
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    def _build_command(self, source_url: str) -> list[str]:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-rw_timeout",
            "15000000", # 15 saniye timeout (mikrosaniye)
            "-analyzeduration",
            "10000000", # 10 saniye analiz
            "-probesize",
            "10000000",
        ]

        if source_url.startswith(("http://", "https://")):
            command.extend(
                [
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "10",
                    "-reconnect_at_eof",
                    "1",
                    "-reconnect_on_network_error",
                    "1",
                    "-reconnect_on_http_error",
                    "4xx,5xx",
                ]
            )

        command.extend(
            [
                "-fflags",
                "nobuffer",
                "-flags",
                "low_delay",
                "-i",
                source_url,
                "-map",
                "0:a:0?",
                "-vn",
                "-sn",
                "-dn",
                "-ac",
                str(self._settings.channels),
                "-ar",
                str(self._settings.sample_rate),
                "-f",
                "s16le",
                "pipe:1",
            ]
        )
        return command

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return

        while True:
            line = await process.stderr.readline()
            if not line:
                return
            logger.warning("ffmpeg: %s", line.decode("utf-8", errors="replace").strip())

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
