from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from live_subtitle_service import __version__
from live_subtitle_service.api.responses import UTF8JSONResponse
from live_subtitle_service.api.router import router
from live_subtitle_service.config import Settings, get_settings
from live_subtitle_service.logging import configure_logging
from live_subtitle_service.services.ffmpeg_source import FFmpegPCMChunkSource
from live_subtitle_service.services.manager import StreamManager
from live_subtitle_service.services.runner import SubtitleSessionRunner
from live_subtitle_service.transcription.local_whisper import LocalWhisperTranscriptionClient


def create_app(
    settings: Settings | None = None,
    manager: StreamManager | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    transcriber: LocalWhisperTranscriptionClient | None = None

    if manager is None:
        transcriber = LocalWhisperTranscriptionClient(resolved_settings)
        audio_source = FFmpegPCMChunkSource(resolved_settings)
        manager = StreamManager(
            settings=resolved_settings,
            runner_factory=lambda: SubtitleSessionRunner(
                audio_source=audio_source,
                transcriber=transcriber,
                settings=resolved_settings,
            ),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved_settings
        app.state.stream_manager = manager
        try:
            yield
        finally:
            await manager.shutdown()
            if transcriber is not None:
                await transcriber.aclose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    cors_origins = _parse_csv(resolved_settings.cors_allowed_origins)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials="*" not in cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router, prefix=resolved_settings.api_prefix)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "live_subtitle_service.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
