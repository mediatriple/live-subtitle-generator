from __future__ import annotations

from live_subtitle_service.config import Settings
from live_subtitle_service.services.ffmpeg_source import FFmpegPCMChunkSource


def test_select_lowest_bandwidth_variant_resolves_relative_url() -> None:
    source = FFmpegPCMChunkSource(Settings())
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
high/playlist.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=640000,RESOLUTION=640x360
low/playlist.m3u8
"""

    assert (
        source._select_lowest_bandwidth_variant(
            "https://cdn.example.com/live/broadcast.smil/playlist.m3u8",
            playlist,
        )
        == "https://cdn.example.com/live/broadcast.smil/low/playlist.m3u8"
    )


def test_select_lowest_bandwidth_variant_ignores_media_playlist() -> None:
    source = FFmpegPCMChunkSource(Settings())
    playlist = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXTINF:4.0,
segment0.ts
"""

    assert (
        source._select_lowest_bandwidth_variant(
            "https://cdn.example.com/live/broadcast.smil/mt_2.m3u8",
            playlist,
        )
        is None
    )
