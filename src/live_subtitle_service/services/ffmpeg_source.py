from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from urllib.parse import urljoin
from urllib.request import Request, urlopen

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
        source_url = await asyncio.to_thread(self._resolve_source_url, request.source_url)
        process = await asyncio.create_subprocess_exec(
            *self._build_command(source_url),
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
            "15000000",  # 15 saniye timeout (mikrosaniye)
            "-analyzeduration",
            "10000000",  # 10 saniye analiz
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

    def _resolve_source_url(self, source_url: str) -> str:
        if not source_url.startswith(("http://", "https://")) or ".m3u8" not in source_url:
            return source_url

        try:
            request = Request(source_url, headers={"User-Agent": "live-subtitle-service/0.1"})
            with urlopen(request, timeout=5) as response:
                playlist = response.read(1024 * 1024).decode("utf-8", errors="replace")
        except Exception:
            logger.warning("failed to inspect HLS playlist; using original source", exc_info=True)
            return source_url

        variant_url = self._select_lowest_bandwidth_variant(source_url, playlist)
        if variant_url:
            logger.info(
                "resolved HLS master playlist",
                extra={"source_url": source_url, "variant_url": variant_url},
            )
            return variant_url

        return source_url

    def _select_lowest_bandwidth_variant(self, base_url: str, playlist: str) -> str | None:
        if "#EXT-X-STREAM-INF" not in playlist:
            return None

        best: tuple[int, str] | None = None
        pending_bandwidth: int | None = None

        for raw_line in playlist.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("#EXT-X-STREAM-INF:"):
                pending_bandwidth = 10**18
                attrs = line.split(":", 1)[1]
                for part in attrs.split(","):
                    key, _, value = part.partition("=")
                    if key.strip().upper() == "BANDWIDTH":
                        try:
                            pending_bandwidth = int(value.strip().strip('"'))
                        except ValueError:
                            pending_bandwidth = 10**18
                        break
                continue

            if pending_bandwidth is None:
                continue

            if not line.startswith("#"):
                candidate = (pending_bandwidth, urljoin(base_url, line))
                if best is None or candidate[0] < best[0]:
                    best = candidate
                pending_bandwidth = None

        return best[1] if best else None

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
