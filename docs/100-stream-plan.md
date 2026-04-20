# 100 Concurrent Stream Plan

## Goal

Build a local-first subtitle platform that can handle roughly 100 concurrent live streams with predictable latency, bounded failure domains, and room for horizontal scaling.

Target service-level objectives:

- End-to-end subtitle latency: under 4 seconds for the default tier
- No unbounded queue growth
- One noisy or broken stream must not degrade unrelated streams
- API and WebSocket delivery must stay available even when ASR workers are saturated

## Why the current design is not enough

The current application is a good single-node prototype, but it will not scale cleanly to 100 live streams because:

- stream state is kept in process memory
- WebSocket fan-out is in process memory
- each stream depends on a local `ffmpeg` process inside the API service
- transcription for the same model runtime is serialized behind one lock

This means the current shape can run multiple streams, but it mixes control plane, ingest, inference, and delivery in one process.

## Target architecture

Split the system into four layers:

1. Control API
   Accepts stream create, stop, status, and history requests.
2. Ingest workers
   Run `ffmpeg`, normalize audio, slice chunks, and publish chunk events.
3. ASR workers
   Pull chunks, run Whisper, deduplicate overlap, and publish subtitle events.
4. Delivery layer
   Serves WebSocket clients and recent subtitle history from shared state.

Supporting infrastructure:

- Redis for short-lived state, pub/sub, and stream queues
- Postgres for durable stream metadata and audit history
- Prometheus and Grafana for metrics
- Optional object storage for raw subtitle archives if retention is needed

## Recommended event flow

1. `POST /streams` creates a stream record in Postgres and a runtime record in Redis.
2. Scheduler assigns the stream to an ingest worker.
3. Ingest worker starts `ffmpeg`, emits chunk messages to a broker topic or stream.
4. Scheduler assigns the stream to one ASR worker for stable ordering.
5. ASR worker loads the configured model once, processes chunks, and writes subtitle events.
6. Delivery nodes subscribe to subtitle events and fan them out to WebSocket clients.
7. API reads current stream state from Redis and durable metadata from Postgres.

## Component responsibilities

### Control API

- stateless application nodes
- validates requests and stores metadata
- does not run `ffmpeg`
- does not run Whisper inference

### Ingest workers

- one worker can supervise many `ffmpeg` child processes
- each stream has a bounded in-memory chunk buffer
- publish only audio chunks and source health events
- if a stream becomes unhealthy, restart only that stream

### ASR workers

- one worker process owns one loaded model profile
- do not mix too many model sizes in one worker pool
- keep stream affinity stable so chunk ordering stays simple
- apply per-stream backpressure and latest-only dropping

### Delivery layer

- reads recent subtitles from Redis
- uses pub/sub for new subtitle events
- can scale horizontally without owning stream execution

## Queue and partition strategy

Use a broker with per-stream ordering. Redis Streams is the simplest first step. NATS JetStream or Kafka is better if throughput and replay needs grow.

Rules:

- one stream maps to one logical partition key
- chunk messages carry `stream_id`, `sequence`, `start_ms`, `end_ms`, `model`, and `language`
- subtitle messages carry the same identifiers plus normalized text
- queues must be bounded by retention and consumer lag alerts

Do not use a global unpartitioned queue for all streams.

## State model

Store hot state in Redis:

- stream status
- worker assignment
- detected language
- recent subtitle ring buffer
- subscriber counters
- worker heartbeats

Store durable state in Postgres:

- stream metadata
- creation and stop events
- source configuration
- operator actions
- optional subtitle archive metadata

## Scheduling and isolation

Use two schedulers:

1. Ingest scheduler
   Assigns new streams to the least-loaded ingest worker.
2. ASR scheduler
   Assigns each stream to a worker pool based on model class and hardware tier.

Isolation rules:

- premium or high-priority streams get a reserved pool
- unknown or experimental sources do not share the same pool as critical streams
- one failing worker should affect only the streams assigned to that worker

## Model strategy

For 100 concurrent streams, do not plan around `large-v3` on CPU.

Recommended tiers:

- default tier: `small`, `medium`, or `turbo`
- high-accuracy tier: GPU-backed worker pool
- language-specific optimization: use fixed language when known to avoid repeated language detection cost

One model profile should map to one worker pool. This keeps capacity planning clear.

## Capacity planning method

Do not guess final worker counts. Benchmark them.

Benchmark matrix:

- model: `small`, `medium`, `turbo`, and any premium model tier
- hardware: CPU node type and GPU node type
- chunk size: 2.5s, 3.0s, 4.0s
- overlap: 0.25s and 0.5s
- languages: at least the top expected languages

Measure:

- real-time factor per worker
- subtitle latency percentiles
- dropped chunk rate
- CPU, RAM, and GPU utilization
- recovery time after source reconnect

Capacity rule:

- keep average worker utilization below 65 percent
- keep p95 utilization below 80 percent
- keep spare capacity for node failure and traffic spikes

## Rollout plan

### Phase 1: decouple the prototype

- move stream state from memory to Redis
- move durable metadata to Postgres
- add stream assignment records
- keep one ASR worker process first, but outside the API

Exit criteria:

- API restart does not kill running streams
- WebSocket delivery survives API node replacement

### Phase 2: introduce a broker

- ingest workers publish chunk events
- ASR workers consume chunk events
- subtitle events are published separately

Exit criteria:

- API nodes no longer run `ffmpeg`
- ASR saturation does not break API responsiveness

### Phase 3: add worker pools

- separate pools by model profile and priority
- add worker heartbeats and rebalancing
- add dead-letter handling for broken streams

Exit criteria:

- one worker failure only impacts its assigned streams
- new workers can be added without downtime

### Phase 4: capacity validation for 100 streams

- run load test with real source simulators
- validate p50, p95, and p99 latency
- validate reconnect storms and worker restarts
- validate subtitles under degraded hardware scenarios

Exit criteria:

- 100 concurrent streams meet target SLOs in staging

## Operational guardrails

- set hard limits per worker for active streams
- kill or reassign streams that exceed failure thresholds
- alert on dropped chunk rate, worker lag, and queue age
- keep subtitle delivery stateless
- keep model downloads out of the hot path by preloading at worker startup

## What to build next in this repository

Recommended implementation order:

1. Redis-backed stream state and subtitle buffers
2. ASR worker process separated from the FastAPI process
3. broker-backed chunk transport
4. worker assignment and heartbeat tracking
5. WebSocket fan-out from Redis pub/sub
6. load-test harness for 50 and 100 concurrent streams

This order keeps the current prototype usable while moving toward a production-safe 100-stream architecture.
