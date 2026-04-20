from __future__ import annotations

import io
import wave

import numpy as np


def pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int, channels: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16)
    return buffer.getvalue()


def pcm16_to_float32_array(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0


def bytes_to_milliseconds(byte_count: int, sample_rate: int, channels: int) -> int:
    bytes_per_second = sample_rate * channels * 2
    return int((byte_count / bytes_per_second) * 1000)
