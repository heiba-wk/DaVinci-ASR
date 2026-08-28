from __future__ import annotations

import unicodedata

from runtime.core.types import TimedToken

COMMA_LIKE = frozenset({",", "，", "、"})
STRONG_OR_CLAUSE_END = frozenset({".", "。", "!", "！", "?", "？", ":", "：", ";", "；"})


def _significant(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(value):
        category = unicodedata.category(char)
        if category.startswith(("L", "N")) or char in {"'", "’"}:
            normalized = unicodedata.normalize("NFKC", char).casefold()
            for item in normalized:
                if unicodedata.category(item).startswith(("L", "N")) or item in {"'", "’"}:
                    characters.append(item)
                    positions.append(index)
    return "".join(characters), positions


def _punctuation(value: str) -> str:
    return "".join(char for char in value if unicodedata.category(char).startswith("P"))


def _normalize_closing_punctuation(value: str) -> str:
    output: list[str] = []
    for char in value:
        if char in COMMA_LIKE and output and output[-1] in STRONG_OR_CLAUSE_END:
            continue
        output.append(char)
    return "".join(output)


def restore_punctuation(transcript: str, tokens: list[TimedToken]) -> list[TimedToken]:
    if not tokens or not transcript:
        return [TimedToken(token.text, token.start, token.end) for token in tokens]
    significant, positions = _significant(transcript)
    if not significant:
        return [TimedToken(token.text, token.start, token.end) for token in tokens]

    spans: list[tuple[int, int] | None] = []
    cursor = 0
    for token in tokens:
        needle, _ = _significant(token.text)
        if not needle:
            spans.append(None)
            continue
        found = significant.find(needle, cursor)
        if found < 0:
            found = significant.find(needle)
        if found < 0:
            spans.append(None)
            continue
        end_sig = found + len(needle) - 1
        spans.append((positions[found], positions[end_sig] + 1))
        cursor = found + len(needle)

    output = [TimedToken(token.text, token.start, token.end) for token in tokens]
    valid_indices = [index for index, span in enumerate(spans) if span is not None]
    if not valid_indices:
        return output

    first = valid_indices[0]
    first_start = spans[first][0]  # type: ignore[index]
    leading = _punctuation(transcript[:first_start])
    if leading:
        output[first].text = leading + output[first].text

    for position, current_index in enumerate(valid_indices):
        current_span = spans[current_index]
        if current_span is None:
            continue
        if position + 1 < len(valid_indices):
            next_index = valid_indices[position + 1]
            next_span = spans[next_index]
            gap_end = next_span[0] if next_span is not None else current_span[1]
        else:
            next_index = -1
            gap_end = len(transcript)
        gap = transcript[current_span[1] : gap_end]
        closing: list[str] = []
        opening: list[str] = []
        for char in gap:
            category = unicodedata.category(char)
            if not category.startswith("P"):
                continue
            if category in {"Ps", "Pi"} and next_index >= 0:
                opening.append(char)
            else:
                closing.append(char)
        if closing:
            output[current_index].text += _normalize_closing_punctuation(
                "".join(closing)
            )
        if opening and next_index >= 0:
            output[next_index].text = "".join(opening) + output[next_index].text
    return output
