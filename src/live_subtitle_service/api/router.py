from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from live_subtitle_service import __version__
from live_subtitle_service.api.schemas import (
    CreateStreamRequest,
    HealthResponse,
    ModelStatusResponse,
    StreamResponse,
    SubtitleListResponse,
    SubtitleSegmentResponse,
)
from live_subtitle_service.config import Settings, normalize_transcription_model
from live_subtitle_service.services.manager import StreamManager, StreamNotFoundError
from live_subtitle_service.transcription.local_whisper import LocalWhisperTranscriptionClient

router = APIRouter()


def get_stream_manager(request: Request) -> StreamManager:
    return request.app.state.stream_manager


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_transcriber(request: Request) -> LocalWhisperTranscriptionClient | None:
    return getattr(request.app.state, "transcriber", None)


@router.get("/healthz", response_model=HealthResponse)
async def healthcheck(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
    )


@router.get("/models", response_model=list[ModelStatusResponse])
async def list_models(
    transcriber: LocalWhisperTranscriptionClient | None = Depends(get_transcriber),
) -> list[ModelStatusResponse]:
    if transcriber is None:
        raise HTTPException(status_code=503, detail="transcriber is not available")

    return [ModelStatusResponse(**item) for item in await transcriber.list_models()]


@router.post("/models/{model_name:path}/download", response_model=ModelStatusResponse)
async def download_model(
    model_name: str,
    transcriber: LocalWhisperTranscriptionClient | None = Depends(get_transcriber),
) -> ModelStatusResponse:
    if transcriber is None:
        raise HTTPException(status_code=503, detail="transcriber is not available")

    normalized = normalize_transcription_model(model_name, fallback="")
    if normalized == "":
        raise HTTPException(status_code=422, detail="unsupported model")

    return ModelStatusResponse(**await transcriber.ensure_model(normalized))


@router.post("/streams", response_model=StreamResponse, status_code=201)
async def create_stream(
    payload: CreateStreamRequest,
    manager: StreamManager = Depends(get_stream_manager),
    settings: Settings = Depends(get_app_settings),
) -> StreamResponse:
    session = await manager.start_stream(payload.to_domain(settings))
    return StreamResponse.from_session(session, settings)


@router.get("/streams", response_model=list[StreamResponse])
async def list_streams(
    manager: StreamManager = Depends(get_stream_manager),
    settings: Settings = Depends(get_app_settings),
) -> list[StreamResponse]:
    sessions = await manager.list_streams()
    return [StreamResponse.from_session(session, settings) for session in sessions]


@router.get("/streams/by-external-id/{external_id}", response_model=StreamResponse)
async def get_stream_by_external_id(
    external_id: str,
    manager: StreamManager = Depends(get_stream_manager),
    settings: Settings = Depends(get_app_settings),
) -> StreamResponse:
    try:
        session = await manager.get_stream_by_external_id(external_id)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=404, detail="stream not found") from exc

    return StreamResponse.from_session(session, settings)


@router.delete("/streams/by-external-id/{external_id}", response_model=StreamResponse)
async def stop_stream_by_external_id(
    external_id: str,
    manager: StreamManager = Depends(get_stream_manager),
    settings: Settings = Depends(get_app_settings),
) -> StreamResponse:
    try:
        session = await manager.stop_stream_by_external_id(external_id)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=404, detail="stream not found") from exc

    return StreamResponse.from_session(session, settings)


@router.get("/streams/{stream_id}", response_model=StreamResponse)
async def get_stream(
    stream_id: str,
    manager: StreamManager = Depends(get_stream_manager),
    settings: Settings = Depends(get_app_settings),
) -> StreamResponse:
    try:
        session = await manager.get_stream(stream_id)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=404, detail="stream not found") from exc

    return StreamResponse.from_session(session, settings)


@router.delete("/streams/{stream_id}", response_model=StreamResponse)
async def stop_stream(
    stream_id: str,
    manager: StreamManager = Depends(get_stream_manager),
    settings: Settings = Depends(get_app_settings),
) -> StreamResponse:
    try:
        session = await manager.stop_stream(stream_id)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=404, detail="stream not found") from exc

    return StreamResponse.from_session(session, settings)


@router.get("/streams/{stream_id}/subtitles", response_model=SubtitleListResponse)
async def get_subtitles(
    stream_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    manager: StreamManager = Depends(get_stream_manager),
) -> SubtitleListResponse:
    try:
        session = await manager.get_stream(stream_id)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=404, detail="stream not found") from exc

    return SubtitleListResponse(
        stream_id=stream_id,
        items=[
            SubtitleSegmentResponse.from_segment(item, session.started_at)
            for item in session.recent_segments(limit)
        ],
    )


@router.websocket("/streams/{stream_id}/ws")
async def stream_websocket(websocket: WebSocket, stream_id: str) -> None:
    manager: StreamManager = websocket.app.state.stream_manager
    settings: Settings = websocket.app.state.settings

    try:
        session = await manager.get_stream(stream_id)
    except StreamNotFoundError:
        await websocket.close(code=4404, reason="stream not found")
        return

    await websocket.accept()
    queue = session.subscribe()

    try:
        await websocket.send_json(
            {
                "type": "snapshot",
                "stream": StreamResponse.from_session(session, settings).model_dump(mode="json"),
                "subtitles": [
                    SubtitleSegmentResponse.from_segment(
                        segment,
                        session.started_at,
                    ).model_dump(mode="json")
                    for segment in session.recent_segments(limit=100)
                ],
            }
        )

        while True:
            segment = await queue.get()
            await websocket.send_json(
                {
                    "type": "subtitle",
                    "data": SubtitleSegmentResponse.from_segment(
                        segment,
                        session.started_at,
                    ).model_dump(mode="json"),
                }
            )
    except WebSocketDisconnect:
        return
    finally:
        session.unsubscribe(queue)
