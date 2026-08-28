from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache


CJK_WORD_LANGUAGES = frozenset({"Chinese", "Cantonese"})
WORD_PATTERN = re.compile(
    r"[^\W_]+(?:['’][^\W_]+)*(?:-[^\W_]+(?:['’][^\W_]+)*)*",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class LexicalSpan:
    text: str
    start: int
    end: int


def _counts_as_word(text: str) -> bool:
    return any(character.isalnum() for character in text)


@lru_cache(maxsize=1)
def _jieba_tokenizer():
    try:
        import jieba
    except ImportError as exc:  # pragma: no cover - release packaging guard
        raise RuntimeError(
            "Chinese and Cantonese subtitle word segmentation requires jieba"
        ) from exc
    jieba.setLogLevel(logging.WARNING)
    tokenizer = jieba.Tokenizer()
    tokenizer.initialize()
    return tokenizer


def _jieba_spans(text: str) -> tuple[LexicalSpan, ...]:
    spans: list[LexicalSpan] = []
    for word, start, end in _jieba_tokenizer().tokenize(
        text, mode="default", HMM=True
    ):
        if start < end and _counts_as_word(word):
            spans.append(LexicalSpan(word, int(start), int(end)))
    return tuple(spans)


def _japanese_spans(text: str) -> tuple[LexicalSpan, ...]:
    try:
        import nagisa
    except ImportError as exc:  # pragma: no cover - release packaging guard
        raise RuntimeError("Japanese subtitle word segmentation requires nagisa") from exc

    spans: list[LexicalSpan] = []
    cursor = 0
    for word in nagisa.tagging(text).words:
        if not word:
            continue
        start = text.find(word, cursor)
        if start < 0:
            # nagisa can normalize uncommon whitespace. A forward search keeps
            # every emitted word mapped without inventing text boundaries.
            start = text.find(word)
        if start < 0:
            continue
        end = start + len(word)
        cursor = end
        if _counts_as_word(word):
            spans.append(LexicalSpan(word, start, end))
    return tuple(spans)


def _unicode_word_spans(text: str) -> tuple[LexicalSpan, ...]:
    return tuple(
        LexicalSpan(match.group(0), match.start(), match.end())
        for match in WORD_PATTERN.finditer(text)
    )


@lru_cache(maxsize=64)
def lexical_spans(text: str, language: str) -> tuple[LexicalSpan, ...]:
    """Return natural word spans without changing the rendered transcript.

    Mandarin and Cantonese use jieba precise mode, Japanese keeps the project's
    existing nagisa tokenizer, and space-delimited languages use Unicode words.
    These spans protect natural word boundaries; subtitle capacity itself is
    measured separately in visual display units.
    """
    if not text:
        return ()
    if language in CJK_WORD_LANGUAGES:
        return _jieba_spans(text)
    if language == "Japanese":
        return _japanese_spans(text)
    return _unicode_word_spans(text)


def count_lexical_words(text: str, language: str) -> int:
    return len(lexical_spans(text, language))
