from live_subtitle_service.utils.text import trim_overlapping_prefix


def test_trim_overlapping_prefix_removes_duplicate_words() -> None:
    assert trim_overlapping_prefix("merhaba nasilsin", "nasilsin bugun") == "bugun"


def test_trim_overlapping_prefix_returns_empty_when_fully_duplicated() -> None:
    assert trim_overlapping_prefix("merhaba dunya", "merhaba dunya") == ""
