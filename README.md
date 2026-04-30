# Live Subtitle Service

An async subtitle service that ingests a live stream with `ffmpeg`, slices audio into short windows, transcribes each window with a local Whisper model, deduplicates overlap, and publishes subtitles over HTTP and WebSocket.

## Why this design

- It is optimized for server-side live stream inputs such as HLS, RTMP, or any other `ffmpeg`-compatible source.
- It is fully local. No OpenAI API key or cloud transcription call is required.
- It uses `faster-whisper` as the runtime because the official repository describes it as a CTranslate2-based Whisper implementation with lower memory use and higher throughput than `openai/whisper` for many server workloads.
- It keeps latency predictable with short chunks, bounded queues, and backpressure-aware dropping when the transcriber falls behind.
- It maps only the audio stream in `ffmpeg`, which is important for noisy live HLS playlists with broken or lagging video renditions.

Local Whisper references:

- `faster-whisper`: https://github.com/SYSTRAN/faster-whisper
- `openai/whisper`: https://github.com/openai/whisper

## Architecture

1. `ffmpeg` reads the live input and emits `pcm16` mono audio at 16kHz.
2. `FFmpegPCMChunkSource` creates overlapping chunks.
3. `LocalWhisperTranscriptionClient` loads a local Whisper model and transcribes each chunk on the host machine.
4. `SubtitleSessionRunner` removes transcript overlap and stores the emitted subtitle segments.
5. `FastAPI` exposes stream lifecycle endpoints plus a WebSocket feed for live subtitle delivery.

## API

- `GET /api/v1/healthz`
- `POST /api/v1/streams`
- `GET /api/v1/streams`
- `GET /api/v1/streams/{stream_id}`
- `GET /api/v1/streams/by-external-id/{external_id}`
- `DELETE /api/v1/streams/{stream_id}`
- `DELETE /api/v1/streams/by-external-id/{external_id}`
- `GET /api/v1/streams/{stream_id}/subtitles`
- `WS  /api/v1/streams/{stream_id}/ws`

### Create a stream

```bash
curl -X POST http://localhost:8000/api/v1/streams \
  -H "Content-Type: application/json" \
  -d '{
    "source_url": "https://example.com/live/playlist.m3u8",
    "external_id": "videoonly-v2:live-crop:account-uuid:broadcast-id",
    "language": "tr",
    "model": "medium",
    "chunk_seconds": 4,
    "overlap_seconds": 0.75
  }'
```

### WebSocket message format

Snapshot message:

```json
{
  "type": "snapshot",
  "stream": {
    "id": "f3f3...",
    "external_id": "videoonly-v2:live-crop:account-uuid:broadcast-id",
    "status": "running",
    "websocket_url": "ws://localhost:8000/api/v1/streams/f3f3.../ws",
    "subtitles_url": "http://localhost:8000/api/v1/streams/f3f3.../subtitles"
  },
  "subtitles": []
}
```

Incremental subtitle message:

```json
{
  "type": "subtitle",
  "data": {
    "stream_id": "f3f3...",
    "sequence": 12,
    "start_ms": 33000,
    "end_ms": 37000,
    "text": "new subtitle text",
    "raw_text": "full raw chunk transcript",
    "created_at": "2026-04-18T00:00:00+00:00"
  }
}
```

## Local development

Prerequisites:

- Python 3.11+
- `ffmpeg`

Setup:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

First run notes:

- On first use, the selected Whisper model is downloaded into `LSS_WHISPER_DOWNLOAD_ROOT`.
- For a CPU-only machine, use `tiny`, `base`, `small`, or `medium` depending on the latency budget.
- Defaults are tuned for CPU execution: `device=cpu`, `compute_type=int8`.

Run:

```bash
live-subtitle-service
```

Test:

```bash
pytest
```

Lint:

```bash
ruff check .
```

## Videoonly v2 integration

`videoonly-v2` can call this service from Laravel and let the browser subscribe to
the returned WebSocket URL. Use a stable `external_id` per broadcast/account; if
that stream is already running, `POST /streams` returns the active session instead
of starting a second `ffmpeg` process.

Recommended local settings:

```env
LSS_PUBLIC_HTTP_BASE_URL=http://localhost:8000
LSS_PUBLIC_WS_BASE_URL=ws://localhost:8000
LSS_CORS_ALLOWED_ORIGINS=http://localhost:8590,http://127.0.0.1:8590
```

When running through `videoonly-v2/docker-compose.yml`, the Laravel container uses
`http://live-subtitles:8000` internally and the browser uses `ws://localhost:8000`
for live updates.

## Kubernetes

The Kubernetes manifest is in [k8s/live-subtitles.yaml](k8s/live-subtitles.yaml).
It creates:

- `Namespace` `videoonly`
- `ConfigMap` for `LSS_*` settings
- `PersistentVolumeClaim` for the Whisper model cache
- `Deployment` with one replica
- `ClusterIP` service exposed through `externalIPs` on `185.70.96.4:8000`

Before applying, adjust these values if the cluster IP or panel host changes:

- image: `mediatriple/live-subtitle-generator:latest`
- public HTTP base URL: `http://185.70.96.4:8000`
- public WebSocket base URL: `ws://185.70.96.4:8000`
- CORS origins: `https://panelpp.mediatriple.net,http://panelpp.mediatriple.net`

Apply:

```bash
kubectl apply -f k8s/live-subtitles.yaml
```

For `videoonly-v2`, set:

```env
LIVE_SUBTITLE_ENABLED=true
LIVE_SUBTITLE_BASE_URL=http://live-subtitles.videoonly.svc.cluster.local:8000
LIVE_SUBTITLE_PUBLIC_BASE_URL=http://185.70.96.4:8000
LIVE_SUBTITLE_PUBLIC_WS_BASE_URL=ws://185.70.96.4:8000
```

If the `videoonly-v2` panel is served over HTTPS, browsers can block plain
`ws://` connections as mixed content. In that case use a TLS-terminating
endpoint for the public WebSocket URL.

The manifest intentionally keeps `replicas: 1` because stream/session state is
currently in memory. If you raise the replica count, WebSocket traffic must be
sticky to the pod that created the stream, or stream state must move to Redis or
a similar shared store.

## Operational notes

- Source URLs must be readable by `ffmpeg`.
- The `model` field accepts `tiny`, `base`, `small`, or `medium`; `medium` is the largest supported live subtitle model.
- The service currently stores stream state and subtitle history in memory. For multi-instance deployment, move stream/session state to Redis or a database and use a pub/sub layer for WebSocket fan-out.
- If you need higher throughput later, keep the same service shape and scale by running multiple workers or assigning different streams to separate Whisper model processes.

## Scaling to multiple live streams

The current code can manage multiple streams in one process, but it is important to understand the limit:

- `StreamManager` can start many stream sessions in parallel.
- Each session has its own bounded chunk queue, so a slow transcriber does not block `ffmpeg` ingestion forever.
- `LocalWhisperTranscriptionClient` serializes inference per loaded model runtime, which means streams using the same model compete for the same transcription lock.

For a small deployment, this is enough:

- Run one API process.
- Use `tiny`, `base`, `small`, or `medium` on CPU.
- Keep queues small so the system drops old chunks instead of building unbounded latency.
- If one stream is business-critical, give it a dedicated process instead of mixing it with lower-priority streams.

For a production multi-stream deployment, split responsibilities:

1. Ingest workers
   Each worker runs `ffmpeg`, normalizes audio, creates chunks, and pushes them to a broker.
2. ASR workers
   Separate worker processes pull chunks from the broker and run Whisper inference on CPU or GPU.
3. API and fan-out layer
   A lightweight API process serves stream status, history, and WebSocket clients without also carrying the transcription load.
4. Shared state
   Store stream metadata, detected language, and recent subtitles in Redis or a database rather than in memory.

Recommended production pattern:

- Use Redis Streams, NATS, or Kafka between ingest and transcription.
- Route each stream to a stable ASR worker to preserve ordering and reduce cross-worker coordination.
- Group workers by model profile, for example `small` and `medium` worker pools.
- Keep one model loaded per worker process instead of reloading per request.
- Publish subtitle events through Redis pub/sub or a similar bus so WebSocket servers can scale horizontally.

Practical capacity advice:

- Models larger than `medium` are intentionally not supported for live subtitle streams.
- CPU fleets should prefer `small` or `medium`, depending on the latency budget.
- If you want many concurrent streams with higher accuracy, move inference to GPU and scale by worker count per GPU.
- If you run on Apple Silicon, benchmark `whisper.cpp` with Metal or `mlx-whisper` against `faster-whisper` before standardizing the runtime.

For a concrete 100-concurrent-stream rollout plan, see [docs/100-stream-plan.md](docs/100-stream-plan.md).
