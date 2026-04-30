from __future__ import annotations

from live_subtitle_service.config import Settings


def test_settings_clamps_unsupported_default_transcription_model() -> None:
    settings = Settings(default_transcription_model="large-v3")

    assert settings.default_transcription_model == "medium"
