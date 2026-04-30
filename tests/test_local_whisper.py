from __future__ import annotations

from live_subtitle_service.config import Settings
from live_subtitle_service.transcription.local_whisper import LocalWhisperTranscriptionClient


async def test_ensure_model_marks_model_downloaded(tmp_path, monkeypatch) -> None:
    settings = Settings(whisper_download_root=str(tmp_path))
    client = LocalWhisperTranscriptionClient(settings)

    monkeypatch.setattr(client, "_create_model", lambda model_name: object())

    status = await client.ensure_model("large-v3")

    assert status == {
        "name": "large-v3",
        "downloaded": True,
        "loaded": True,
    }
    assert client.is_model_downloaded("large-v3") is True


async def test_list_models_reports_download_markers(tmp_path) -> None:
    settings = Settings(whisper_download_root=str(tmp_path))
    marker_dir = tmp_path / ".videoonly-models"
    marker_dir.mkdir()
    (marker_dir / "medium.ready").write_text("ready\n", encoding="utf-8")

    client = LocalWhisperTranscriptionClient(settings)
    models = await client.list_models()
    medium = next(item for item in models if item["name"] == "medium")

    assert medium["downloaded"] is True
    assert medium["loaded"] is False
