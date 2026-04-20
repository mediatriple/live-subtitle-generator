from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LSS_",
        extra="ignore",
    )

    app_name: str = "live-subtitle-service"
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    public_http_base_url: str | None = None
    public_ws_base_url: str | None = None
    cors_allowed_origins: str = ""

    default_transcription_model: str = "small"
    transcription_device: str = "cpu"
    transcription_compute_type: str = "int8"
    transcription_cpu_threads: int = 0
    transcription_num_workers: int = 1
    transcription_beam_size: int = 1
    transcription_condition_on_previous_text: bool = False
    transcription_vad_filter: bool = True
    transcription_language_lock_min_probability: float = 0.8
    whisper_download_root: str | None = ".cache/whisper"

    sample_rate: int = 16000
    channels: int = 1
    chunk_seconds: float = 4.0
    overlap_seconds: float = 0.75
    chunk_queue_size: int = 4
    subscriber_queue_size: int = 50
    max_segments_per_stream: int = 500
    shutdown_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def validate_audio_window(self) -> Settings:
        if self.chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be greater than zero")
        if self.overlap_seconds < 0:
            raise ValueError("overlap_seconds must be zero or greater")
        if self.chunk_seconds <= self.overlap_seconds:
            raise ValueError("chunk_seconds must be greater than overlap_seconds")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")
        if self.channels <= 0:
            raise ValueError("channels must be greater than zero")
        if self.transcription_cpu_threads < 0:
            raise ValueError("transcription_cpu_threads must be zero or greater")
        if self.transcription_num_workers <= 0:
            raise ValueError("transcription_num_workers must be greater than zero")
        if self.transcription_beam_size <= 0:
            raise ValueError("transcription_beam_size must be greater than zero")
        if not 0 <= self.transcription_language_lock_min_probability <= 1:
            raise ValueError("transcription_language_lock_min_probability must be between 0 and 1")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
