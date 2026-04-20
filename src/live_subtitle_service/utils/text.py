from __future__ import annotations

import re

TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_transcript_text(text: str) -> str:
    cleaned = WHITESPACE_PATTERN.sub(" ", text).strip()
    return cleaned


def trim_overlapping_prefix(previous_text: str, current_text: str, max_tokens: int = 18) -> str:
    previous_tokens = list(TOKEN_PATTERN.finditer(previous_text))
    current_tokens = list(TOKEN_PATTERN.finditer(current_text))

    if not previous_tokens or not current_tokens:
        return normalize_transcript_text(current_text)

    previous_normalized = [match.group(0).casefold() for match in previous_tokens]
    current_normalized = [match.group(0).casefold() for match in current_tokens]
    max_overlap = min(max_tokens, len(previous_normalized), len(current_normalized))

    for overlap in range(max_overlap, 0, -1):
        if previous_normalized[-overlap:] != current_normalized[:overlap]:
            continue

        cut_index = current_tokens[overlap - 1].end()
        trimmed = current_text[cut_index:].lstrip(" ,.;:!?-")
        return normalize_transcript_text(trimmed)

    return normalize_transcript_text(current_text)
