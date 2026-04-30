from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from live_subtitle_service.config import ALLOWED_TRANSCRIPTION_MODELS, Settings
from live_subtitle_service.domain.models import AudioChunk, StreamRequest, TranscriptionResult
from live_subtitle_service.utils.audio import pcm16_to_float32_array

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelRuntime:
    model: WhisperModel
    lock: asyncio.Lock


class LocalWhisperTranscriptionClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models: dict[str, ModelRuntime] = {}
        self._models_lock = asyncio.Lock()

    async def aclose(self) -> None:
        return None

    async def list_models(self) -> list[dict[str, object]]:
        loaded = set(self._models.keys())
        return [
            {
                "name": model_name,
                "downloaded": self.is_model_downloaded(model_name),
                "loaded": model_name in loaded,
            }
            for model_name in ALLOWED_TRANSCRIPTION_MODELS
        ]

    async def ensure_model(self, model_name: str) -> dict[str, object]:
        await self._get_runtime(model_name)
        self._write_download_marker(model_name)
        return {
            "name": model_name,
            "downloaded": self.is_model_downloaded(model_name),
            "loaded": model_name in self._models,
        }

    async def transcribe(
        self,
        chunk: AudioChunk,
        request: StreamRequest,
    ) -> TranscriptionResult:
        runtime = await self._get_runtime(request.model)
        audio = pcm16_to_float32_array(chunk.pcm16)

        async with runtime.lock:
            return await asyncio.to_thread(
                self._transcribe_sync,
                runtime.model,
                audio,
                request.language,
            )

    async def _get_runtime(self, model_name: str) -> ModelRuntime:
        async with self._models_lock:
            runtime = self._models.get(model_name)
            if runtime is not None:
                return runtime

            logger.info("Loading local Whisper model", extra={"model": model_name})
            model = await asyncio.to_thread(self._create_model, model_name)
            runtime = ModelRuntime(model=model, lock=asyncio.Lock())
            self._models[model_name] = runtime
            return runtime

    def _create_model(self, model_name: str) -> WhisperModel:
        kwargs: dict[str, object] = {
            "device": self._settings.transcription_device,
            "compute_type": self._settings.transcription_compute_type,
            "num_workers": self._settings.transcription_num_workers,
        }
        if self._settings.transcription_cpu_threads > 0:
            kwargs["cpu_threads"] = self._settings.transcription_cpu_threads
        if self._settings.whisper_download_root:
            kwargs["download_root"] = self._settings.whisper_download_root

        return WhisperModel(model_name, **kwargs)

    def is_model_downloaded(self, model_name: str) -> bool:
        if model_name in self._models:
            return True

        if self._download_marker_path(model_name).is_file():
            return True

        return self._model_cache_path(model_name).exists()

    def _write_download_marker(self, model_name: str) -> None:
        marker = self._download_marker_path(model_name)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("ready\n", encoding="utf-8")
        except OSError:
            logger.warning("Unable to write model download marker", extra={"model": model_name})

    def _download_marker_path(self, model_name: str) -> Path:
        root = Path(self._settings.whisper_download_root or ".cache/whisper")
        return root / ".videoonly-models" / f"{self._safe_model_key(model_name)}.ready"

    def _model_cache_path(self, model_name: str) -> Path:
        root = Path(self._settings.whisper_download_root or ".cache/whisper")
        return root / f"models--Systran--faster-whisper-{model_name}"

    def _safe_model_key(self, model_name: str) -> str:
        return model_name.replace("/", "__").replace(":", "_")

    def _transcribe_sync(
        self,
        model: WhisperModel,
        audio,
        language: str | None,
    ) -> TranscriptionResult:
        segments, info = model.transcribe(
            audio,
            language=language,
            beam_size=self._settings.transcription_beam_size,
            condition_on_previous_text=self._settings.transcription_condition_on_previous_text,
            vad_filter=self._settings.transcription_vad_filter,
        )
        transcript_parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        detected_language = None
        detected_language_probability = None
        if language is None:
            detected_language = info.language
            detected_language_probability = info.language_probability

        return TranscriptionResult(
            text=" ".join(transcript_parts).strip(),
            detected_language=detected_language,
            detected_language_probability=detected_language_probability,
        )
