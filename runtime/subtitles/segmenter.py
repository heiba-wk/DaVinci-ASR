from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from wcwidth import wcswidth, wcwidth

from runtime.core.types import SubtitleBlock, TimedToken
from runtime.subtitles.language_profiles import (
    NO_SPACE_LANGUAGES,
    LanguageProfile,
    analyze_language_boundary,
    get_language_profile,
    normalize_lexical_unit,
)
from runtime.subtitles.word_units import lexical_spans

SENTENCE_END = frozenset(".?!。？！…")
CLAUSE_END = frozenset(",;:，；：、")
PROTECTED_BOUNDARY_SCORE = -1_000
SENTENCE_BOUNDARY_SCORE = 1_000
CLAUSE_BOUNDARY_SCORE = 80
CONJUNCTION_BOUNDARY_SCORE = 50
SUBORDINATE_BOUNDARY_SCORE = 40
PREPOSITION_BOUNDARY_SCORE = 45
BOUNDARY_PHRASE_SCORE = 65
CJK_BOUNDARY_SCORE = 20
WEAK_PAUSE_SECONDS = 0.30
MEDIUM_PAUSE_SECONDS = 0.50
STRONG_PAUSE_SECONDS = 0.80
FORCED_PAUSE_SECONDS = 1.50
WEAK_PAUSE_SCORE = 8
MEDIUM_PAUSE_SCORE = 40
STRONG_PAUSE_SCORE = 100
MAX_POSITION_SCORE = 20
PROTECTED_BOUNDARY_PENALTY = 600
PROTECTED_PHRASE_PENALTY = 300
TINY_FRAGMENT_PENALTY = 220
PLANNING_MAX_TOKENS = 1_024
PLANNING_MAX_DISPLAY_UNIT_MULTIPLIER = 3
PLANNING_MIN_DISPLAY_UNITS = 12
DP_BLOCK_PENALTY = 500
DP_MAX_FILL_REWARD = 30
DP_UNUSED_WIDTH_PENALTY = 20
DP_SHORT_BLOCK_RATIO = 0.35
DP_SHORT_BLOCK_PENALTY = 60
PREFERRED_BOUNDARY_BONUS = 30
CJK_PREFERRED_BOUNDARY_BONUS = 140


@dataclass(frozen=True, slots=True)
class BoundaryFeatures:
    sentence_end: bool
    clause_end: bool
    pause_seconds: float
    before_conjunction: bool
    before_subordinate: bool
    before_preposition: bool
    boundary_phrase: bool
    protected_boundary: bool
    protected_phrase: bool
    matched_boundary_phrase: tuple[str, ...]
    matched_protected_phrase: tuple[str, ...]
    protection_reasons: tuple[str, ...]
    position_ratio: float
    tiny_fragment: bool

    @property
    def preferred_boundary(self) -> bool:
        return bool(
            self.sentence_end
            or self.clause_end
            or self.pause_seconds >= WEAK_PAUSE_SECONDS
            or self.before_conjunction
            or self.before_subordinate
            or self.before_preposition
            or self.boundary_phrase
        )


def _is_closing_punctuation(char: str) -> bool:
    return bool(char) and (
        char in SENTENCE_END | CLAUSE_END or unicodedata.category(char) in {"Pe", "Pf"}
    )


def _is_opening_punctuation(char: str) -> bool:
    return bool(char) and unicodedata.category(char) in {"Ps", "Pi"}


def _joiner(previous: str, current: str, language: str) -> str:
    if language in NO_SPACE_LANGUAGES:
        return ""
    if not previous or not current:
        return ""
    if _is_closing_punctuation(current[0]) or _is_opening_punctuation(previous[-1]):
        return ""
    return " "


def join_token_text(tokens: list[TimedToken], language: str) -> str:
    parts: list[str] = []
    previous = ""
    for token in tokens:
        text = token.text.strip()
        if not text:
            continue
        if parts:
            parts.append(_joiner(previous, text, language))
        parts.append(text)
        previous = text
    return "".join(parts).strip()


def _ends_with(text: str, characters: frozenset[str]) -> bool:
    stripped = text.rstrip()
    while stripped and unicodedata.category(stripped[-1]) in {"Pe", "Pf"}:
        stripped = stripped[:-1].rstrip()
    return bool(stripped and stripped[-1] in characters)


def _trim_end_punctuation(text: str) -> str:
    value = text.rstrip()
    while value and unicodedata.category(value[-1]).startswith("P"):
        value = value[:-1].rstrip()
    return value


def postprocess_subtitle_blocks(
    blocks: list[SubtitleBlock],
    *,
    remove_gaps: bool = False,
    trim_end_punctuation: bool = False,
) -> list[SubtitleBlock]:
    if trim_end_punctuation:
        for block in blocks:
            block.text = _trim_end_punctuation(block.text)
    output = [block for block in blocks if block.text]
    if remove_gaps:
        for index in range(len(output) - 1):
            if output[index + 1].start > output[index].start:
                output[index].end = output[index + 1].start
    return output


def display_units(text: str) -> int:
    """Return the terminal-style visual width of rendered subtitle text."""
    width = wcswidth(text)
    if width >= 0:
        return width
    return sum(max(0, wcwidth(character)) for character in text)


def effective_max_chars(max_chars: int, language: str) -> int:
    """Return the unchanged File-IPC limit, now interpreted as display units."""
    return max(1, int(max_chars))


def _lexical_edges(value: str) -> tuple[str, str]:
    words = re.findall(
        r"[^\W_]+(?:['’][^\W_]+)*",
        normalize_lexical_unit(value),
        re.UNICODE,
    )
    if not words:
        return "", ""
    return words[0], words[-1]


def _boundary_profile(language: str) -> LanguageProfile:
    """Compatibility accessor for the independent 11-language registry."""
    return get_language_profile(language)


def _pause_seconds(current: TimedToken, following: TimedToken | None) -> float:
    if following is None:
        return 0.0
    return max(0.0, following.start - current.end)


def _numeric_continuation(
    current: TimedToken,
    following: TimedToken | None,
    language: str | None = None,
) -> bool:
    if following is None:
        return False
    left = current.text.strip()
    right = following.text.strip()
    decimal_continuation = bool(
        len(left) >= 2
        and left[-1] in {".", ",", "，", "．"}
        and left[-2].isdigit()
        and right
        and right[0].isdigit()
    )
    if decimal_continuation or language is None:
        return decimal_continuation
    if not (
        len(left) >= 2 and left[-1] in {".", "．"} and left[-2].isdigit() and right
    ):
        return False
    following_first, _ = _lexical_edges(right)
    _, current_last = _lexical_edges(left)
    analysis = analyze_language_boundary(
        (current_last, following_first),
        1,
        _boundary_profile(language),
    )
    return "number_unit" in analysis.protection_reasons


def _is_sentence_boundary(
    current: TimedToken,
    following: TimedToken | None,
    language: str | None = None,
) -> bool:
    return bool(
        _ends_with(current.text, SENTENCE_END)
        and not _numeric_continuation(current, following, language)
    )


def _pause_score(pause: float) -> int:
    if pause >= STRONG_PAUSE_SECONDS:
        return STRONG_PAUSE_SCORE
    if pause >= MEDIUM_PAUSE_SECONDS:
        return MEDIUM_PAUSE_SCORE
    if pause >= WEAK_PAUSE_SECONDS:
        return WEAK_PAUSE_SCORE
    return 0


def _semantic_boundary_score(
    current: TimedToken,
    following: TimedToken | None,
    language: str,
) -> int:
    current_text = current.text.strip()
    following_clean = following.text.strip() if following is not None else ""
    following_first, _ = _lexical_edges(following_clean)
    _, current_last = _lexical_edges(current_text)
    profile = _boundary_profile(language)
    if _is_sentence_boundary(current, following, language):
        return SENTENCE_BOUNDARY_SCORE
    if _ends_with(current_text, CLAUSE_END) and not _numeric_continuation(
        current, following, language
    ):
        return CLAUSE_BOUNDARY_SCORE
    analysis = analyze_language_boundary((current_last, following_first), 1, profile)
    if analysis.protected_boundary or _numeric_continuation(
        current, following, language
    ):
        return PROTECTED_BOUNDARY_SCORE
    if analysis.boundary_phrase:
        return BOUNDARY_PHRASE_SCORE
    if analysis.before_conjunction:
        return CONJUNCTION_BOUNDARY_SCORE
    if analysis.before_subordinate:
        return SUBORDINATE_BOUNDARY_SCORE
    if analysis.before_preposition:
        return PREPOSITION_BOUNDARY_SCORE
    if language in NO_SPACE_LANGUAGES and len(current_text) >= 2:
        return CJK_BOUNDARY_SCORE
    return 0


def _boundary_score(
    current: TimedToken,
    following: TimedToken | None,
    language: str,
) -> int:
    semantic_score = _semantic_boundary_score(current, following, language)
    if semantic_score in {PROTECTED_BOUNDARY_SCORE, SENTENCE_BOUNDARY_SCORE}:
        return semantic_score
    return semantic_score + _pause_score(_pause_seconds(current, following))


def _following_phrase_score(
    words: list[TimedToken] | tuple[TimedToken, ...],
    next_index: int,
    language: str,
) -> int:
    """Recognize 2-4 lexical-unit phrases for all supported languages."""
    if next_index >= len(words):
        return 0
    profile = _boundary_profile(language)
    rendered = join_token_text(list(words[next_index:]), language)
    units = tuple(
        normalize_lexical_unit(span.text) for span in lexical_spans(rendered, language)
    )
    analysis = analyze_language_boundary(units, 0, profile)
    if analysis.boundary_phrase:
        return BOUNDARY_PHRASE_SCORE
    if analysis.before_conjunction:
        return CONJUNCTION_BOUNDARY_SCORE
    if analysis.before_subordinate:
        return SUBORDINATE_BOUNDARY_SCORE
    if analysis.before_preposition:
        return PREPOSITION_BOUNDARY_SCORE
    return 0


def score_boundary(features: BoundaryFeatures, profile: LanguageProfile) -> int:
    score = 0
    if features.preferred_boundary:
        score += (
            CJK_PREFERRED_BOUNDARY_BONUS
            if profile.language in NO_SPACE_LANGUAGES
            else PREFERRED_BOUNDARY_BONUS
        )
    if features.sentence_end:
        score += SENTENCE_BOUNDARY_SCORE
    elif features.clause_end:
        score += CLAUSE_BOUNDARY_SCORE
    if features.before_conjunction:
        score += CONJUNCTION_BOUNDARY_SCORE
    if features.before_subordinate:
        score += SUBORDINATE_BOUNDARY_SCORE
    if features.before_preposition:
        score += PREPOSITION_BOUNDARY_SCORE
    if features.boundary_phrase:
        score += BOUNDARY_PHRASE_SCORE
    score += _pause_score(features.pause_seconds)
    score += min(
        MAX_POSITION_SCORE,
        int(round(MAX_POSITION_SCORE * features.position_ratio)),
    )
    if features.tiny_fragment and not features.sentence_end:
        score -= TINY_FRAGMENT_PENALTY
    if features.protected_boundary and not features.sentence_end:
        score -= PROTECTED_BOUNDARY_PENALTY
    if features.protected_phrase and not features.sentence_end:
        score -= PROTECTED_PHRASE_PENALTY
    return score


@dataclass(frozen=True, slots=True)
class SegmentationContext:
    tokens: tuple[TimedToken, ...]
    language: str
    rendered_text: str
    boundary_offsets: tuple[int, ...]
    boundary_display_units: tuple[int, ...]
    boundary_leading_joiner_display_units: tuple[int, ...]
    safe_boundaries: tuple[bool, ...]
    prefix_word_counts: tuple[int, ...]
    lexical_units: tuple[str, ...]
    boundary_left_words: tuple[str, ...]
    boundary_right_words: tuple[str, ...]

    def word_count(self, start: int, end: int) -> int:
        return self.prefix_word_counts[end] - self.prefix_word_counts[start]

    def display_unit_count(self, start: int, end: int) -> int:
        width = self.boundary_display_units[end] - self.boundary_display_units[start]
        if width <= 0:
            return 0
        return width - self.boundary_leading_joiner_display_units[start]

    def is_atomic_span(self, start: int, end: int) -> bool:
        return end == start + 1 or not any(self.safe_boundaries[start + 1 : end])


def _segmentation_context(
    tokens: list[TimedToken] | tuple[TimedToken, ...], language: str
) -> SegmentationContext:
    texts = [token.text.strip() for token in tokens]
    parts: list[str] = []
    offsets = [0]
    display_unit_offsets = [0]
    previous = ""
    rendered_length = 0
    rendered_display_units = 0
    for text in texts:
        if text:
            if parts:
                joiner = _joiner(previous, text, language)
                parts.append(joiner)
                rendered_length += len(joiner)
                rendered_display_units += display_units(joiner)
            parts.append(text)
            rendered_length += len(text)
            rendered_display_units += display_units(text)
            previous = text
        offsets.append(rendered_length)
        display_unit_offsets.append(rendered_display_units)

    next_texts = [""] * (len(texts) + 1)
    following = ""
    for index in range(len(texts) - 1, -1, -1):
        if texts[index]:
            following = texts[index]
        next_texts[index] = following
    leading_joiner_display_units: list[int] = []
    previous = ""
    for index in range(len(texts) + 1):
        leading_joiner_display_units.append(
            display_units(_joiner(previous, next_texts[index], language))
            if previous and next_texts[index]
            else 0
        )
        if index < len(texts) and texts[index]:
            previous = texts[index]

    rendered = "".join(parts).strip()
    spans = lexical_spans(rendered, language)
    lexical_units = tuple(normalize_lexical_unit(span.text) for span in spans)
    safe_boundaries: list[bool] = []
    prefix_word_counts: list[int] = []
    boundary_left_words: list[str] = []
    boundary_right_words: list[str] = []
    completed = 0
    for offset in offsets:
        while completed < len(spans) and spans[completed].end <= offset:
            completed += 1
        active = spans[completed] if completed < len(spans) else None
        safe_boundaries.append(
            active is None or not (active.start < offset < active.end)
        )
        prefix_word_counts.append(completed)
        boundary_left_words.append(
            spans[completed - 1].text.casefold() if completed else ""
        )
        boundary_right_words.append(
            active.text.casefold()
            if active is not None and active.start >= offset
            else ""
        )
    return SegmentationContext(
        tokens=tuple(tokens),
        language=language,
        rendered_text=rendered,
        boundary_offsets=tuple(offsets),
        boundary_display_units=tuple(display_unit_offsets),
        boundary_leading_joiner_display_units=tuple(leading_joiner_display_units),
        safe_boundaries=tuple(safe_boundaries),
        prefix_word_counts=tuple(prefix_word_counts),
        lexical_units=lexical_units,
        boundary_left_words=tuple(boundary_left_words),
        boundary_right_words=tuple(boundary_right_words),
    )


def _minimum_fragment_units(max_units: int) -> int:
    if max_units <= 2:
        return 1
    return min(8, max(4, max_units // 4))


def _block_fill_score(
    unit_count: int,
    max_chars: int,
    *,
    atomic_overflow: bool = False,
) -> int:
    """Cumulative per-block width quality used by the global DP objective."""
    if atomic_overflow:
        return 0
    ratio = min(1.0, max(0.0, unit_count / max(1, max_chars)))
    reward = int(round(DP_MAX_FILL_REWARD * ratio))
    unused_penalty = int(round(DP_UNUSED_WIDTH_PENALTY * ((1.0 - ratio) ** 2)))
    return reward - unused_penalty


def _lexical_neighbors(
    context: SegmentationContext, boundary_index: int
) -> tuple[str, str]:
    return (
        context.boundary_left_words[boundary_index],
        context.boundary_right_words[boundary_index],
    )


def _context_numeric_continuation(
    context: SegmentationContext,
    boundary_index: int,
    *,
    protection_reasons: tuple[str, ...] = (),
) -> bool:
    offset = context.boundary_offsets[boundary_index]
    left = context.rendered_text[:offset].rstrip()
    right = context.rendered_text[offset:].lstrip()
    decimal_continuation = bool(
        len(left) >= 2
        and left[-1] in {".", ",", "，", "．"}
        and left[-2].isdigit()
        and right
        and right[0].isdigit()
    )
    ordinal_continuation = bool(
        len(left) >= 2
        and left[-1] in {".", "．"}
        and left[-2].isdigit()
        and right
        and "number_unit" in protection_reasons
    )
    return decimal_continuation or ordinal_continuation


def _cjk_protected_relation(left_word: str, right_word: str, language: str) -> bool:
    if language not in {"Chinese", "Cantonese"}:
        return False
    profile = get_language_profile(language)
    return analyze_language_boundary(
        (
            normalize_lexical_unit(left_word),
            normalize_lexical_unit(right_word),
        ),
        1,
        profile,
    ).protected_boundary


def _character_boundary_features(
    context: SegmentationContext,
    boundary_index: int,
    *,
    region_start: int,
    region_end: int,
    max_chars: int,
) -> tuple[BoundaryFeatures, LanguageProfile]:
    current = context.tokens[boundary_index - 1]
    following = context.tokens[boundary_index]
    profile = _boundary_profile(context.language)
    current_text = current.text.strip()
    lexical_boundary_index = context.prefix_word_counts[boundary_index]
    analysis = analyze_language_boundary(
        context.lexical_units,
        lexical_boundary_index,
        profile,
    )
    numeric_continuation = _context_numeric_continuation(
        context,
        boundary_index,
        protection_reasons=analysis.protection_reasons,
    )
    left_units = context.display_unit_count(region_start, boundary_index)
    right_units = context.display_unit_count(boundary_index, region_end)
    minimum_fragment = _minimum_fragment_units(max_chars)
    return (
        BoundaryFeatures(
            sentence_end=(
                _ends_with(current_text, SENTENCE_END) and not numeric_continuation
            ),
            clause_end=(
                _ends_with(current_text, CLAUSE_END) and not numeric_continuation
            ),
            pause_seconds=_pause_seconds(current, following),
            before_conjunction=analysis.before_conjunction,
            before_subordinate=analysis.before_subordinate,
            before_preposition=analysis.before_preposition,
            boundary_phrase=analysis.boundary_phrase,
            protected_boundary=(analysis.protected_boundary or numeric_continuation),
            protected_phrase=analysis.protected_phrase,
            matched_boundary_phrase=analysis.matched_boundary_phrase,
            matched_protected_phrase=analysis.matched_protected_phrase,
            protection_reasons=(
                analysis.protection_reasons
                + (("numeric_continuation",) if numeric_continuation else ())
            ),
            position_ratio=min(1.0, left_units / max(1, max_chars)),
            tiny_fragment=(
                left_units < minimum_fragment or right_units < minimum_fragment
            ),
        ),
        profile,
    )


def _planning_region_end(
    context: SegmentationContext,
    start_index: int,
    *,
    max_chars: int,
) -> int:
    end_index = start_index
    # Tiny limits still need enough lookahead to keep the planning-region edge
    # from manufacturing a protected lexical break. The region remains bounded
    # independently by both display units and token count.
    display_unit_cap = max(
        PLANNING_MIN_DISPLAY_UNITS,
        max_chars * PLANNING_MAX_DISPLAY_UNIT_MULTIPLIER,
    )
    last_safe = start_index
    cap_boundary: tuple[int, int] | None = None
    while end_index < len(context.tokens):
        if end_index > start_index and context.safe_boundaries[end_index]:
            pause = _pause_seconds(
                context.tokens[end_index - 1], context.tokens[end_index]
            )
            if pause >= FORCED_PAUSE_SECONDS:
                left_words = context.word_count(start_index, end_index)
                right_words = context.word_count(end_index, len(context.tokens))
                left_offset = context.boundary_offsets[start_index]
                boundary_offset = context.boundary_offsets[end_index]
                right_offset = context.boundary_offsets[-1]
                left_lexical_chars = sum(
                    character.isalnum()
                    for character in context.rendered_text[left_offset:boundary_offset]
                )
                right_lexical_chars = sum(
                    character.isalnum()
                    for character in context.rendered_text[boundary_offset:right_offset]
                )
                features, _ = _character_boundary_features(
                    context,
                    end_index,
                    region_start=start_index,
                    region_end=len(context.tokens),
                    max_chars=max_chars,
                )
                if (
                    left_words >= 1
                    and right_words >= 1
                    and left_lexical_chars >= 4
                    and right_lexical_chars >= 4
                    and not features.protected_boundary
                ):
                    return end_index

        end_index += 1
        if not context.safe_boundaries[end_index]:
            continue
        last_safe = end_index
        current = context.tokens[end_index - 1]
        if _ends_with(current.text, SENTENCE_END):
            if end_index == len(context.tokens):
                return end_index
            features, _ = _character_boundary_features(
                context,
                end_index,
                region_start=start_index,
                region_end=len(context.tokens),
                max_chars=max_chars,
            )
            if features.sentence_end:
                return end_index
        region_units = context.display_unit_count(start_index, end_index)
        if region_units >= display_unit_cap and end_index < len(context.tokens):
            features, profile = _character_boundary_features(
                context,
                end_index,
                region_start=start_index,
                region_end=len(context.tokens),
                max_chars=max_chars,
            )
            candidate = (score_boundary(features, profile), end_index)
            if cap_boundary is None or candidate > cap_boundary:
                cap_boundary = candidate
            if features.preferred_boundary and not features.protected_boundary:
                return end_index
            hard_display_unit_cap = display_unit_cap + max_chars
            if region_units >= hard_display_unit_cap:
                return cap_boundary[1]
        if end_index - start_index >= PLANNING_MAX_TOKENS:
            return cap_boundary[1] if cap_boundary is not None else end_index

    if last_safe <= start_index:
        raise AssertionError("No safe lexical-word boundary exists in planning region")
    return last_safe


def _plan_context_region_dp(
    context: SegmentationContext,
    start_index: int,
    end_index: int,
    *,
    max_chars: int,
) -> list[tuple[int, int]]:
    region_size = end_index - start_index
    if region_size <= 0:
        return []
    minimum_fragment = _minimum_fragment_units(max_chars)
    short_target = max(minimum_fragment, int(round(max_chars * DP_SHORT_BLOCK_RATIO)))
    states: list[tuple[int, tuple[tuple[int, int], ...]] | None] = [None] * (
        region_size + 1
    )
    states[0] = (0, ())
    boundary_scores: dict[int, int] = {}
    for boundary_index in range(start_index + 1, end_index):
        if not context.safe_boundaries[boundary_index]:
            continue
        features, boundary_profile = _character_boundary_features(
            context,
            boundary_index,
            region_start=start_index,
            region_end=end_index,
            max_chars=max_chars,
        )
        boundary_scores[boundary_index] = score_boundary(features, boundary_profile)

    for local_end in range(1, region_size + 1):
        global_end = start_index + local_end
        if not context.safe_boundaries[global_end]:
            continue
        best: tuple[int, tuple[tuple[int, int], ...]] | None = None
        for local_start in range(local_end):
            previous = states[local_start]
            if previous is None:
                continue
            global_start = start_index + local_start
            if not context.safe_boundaries[global_start]:
                continue
            unit_count = context.display_unit_count(global_start, global_end)
            atomic_overflow = unit_count > max_chars and context.is_atomic_span(
                global_start, global_end
            )
            if unit_count > max_chars and not atomic_overflow:
                continue

            score = previous[0] - DP_BLOCK_PENALTY
            score += _block_fill_score(
                unit_count,
                max_chars,
                atomic_overflow=atomic_overflow,
            )
            if unit_count < short_target and region_size > 1:
                score -= DP_SHORT_BLOCK_PENALTY
            if global_end < end_index:
                score += boundary_scores[global_end]
            elif unit_count < minimum_fragment and region_size > 1:
                score -= TINY_FRAGMENT_PENALTY

            spans = previous[1] + ((global_start, global_end),)
            candidate = (score, spans)
            if best is None:
                best = candidate
                continue
            candidate_rank = (candidate[0], -len(candidate[1]), candidate[1])
            best_rank = (best[0], -len(best[1]), best[1])
            if candidate_rank > best_rank:
                best = candidate
        states[local_end] = best

    final = states[-1]
    if final is None:
        raise AssertionError("No atomic max_chars segmentation plan exists")
    return list(final[1])


def _joined_block_text(
    left: SubtitleBlock,
    right: SubtitleBlock,
    language: str,
) -> str:
    return (left.text + _joiner(left.text, right.text, language) + right.text).strip()


def _coalesce_overlapping_blocks(
    blocks: list[SubtitleBlock],
    *,
    language: str,
    max_chars: int,
) -> list[SubtitleBlock]:
    """Prevent overlap quantization from collapsing a readable block to one frame."""
    working = [SubtitleBlock(block.start, block.end, block.text) for block in blocks]
    while len(working) >= 2:
        changed = False
        for index in range(len(working) - 1):
            left = working[index]
            right = working[index + 1]
            if right.start + 1e-9 >= left.end:
                continue
            combined = _joined_block_text(left, right, language)
            if display_units(combined) > max_chars:
                continue
            working[index : index + 2] = [
                SubtitleBlock(
                    min(left.start, right.start),
                    max(left.end, right.end),
                    combined,
                )
            ]
            changed = True
            break
        if changed:
            continue

        # If an overlapping fragment cannot fit in the preceding block, attach
        # it to a nearby following block when that preserves the character
        # limit. This retains text order while giving frame quantization a
        # reliable interval that is not already consumed by the previous block.
        for index in range(1, len(working) - 1):
            previous = working[index - 1]
            current = working[index]
            following = working[index + 1]
            mostly_consumed = (
                current.start + 1e-9 < previous.end
                and current.end <= previous.end + WEAK_PAUSE_SECONDS
            )
            following_is_near = following.start <= current.end + WEAK_PAUSE_SECONDS
            combined = _joined_block_text(current, following, language)
            if (
                not mostly_consumed
                or not following_is_near
                or display_units(combined) > max_chars
            ):
                continue
            working[index : index + 2] = [
                SubtitleBlock(
                    min(current.start, following.start),
                    max(current.end, following.end),
                    combined,
                )
            ]
            changed = True
            break
        if not changed:
            break
    return working


def _plan_region_greedy(
    words: list[TimedToken],
    *,
    language: str,
    max_chars: int,
) -> list[tuple[int, int]]:
    """Compatibility helper using the production character-limit contract."""
    context = _segmentation_context(words, language)
    character_limit = effective_max_chars(max_chars, language)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(words):
        end = start + 1
        best_end: int | None = None
        while end <= len(words):
            if context.safe_boundaries[end]:
                unit_count = context.display_unit_count(start, end)
                atomic_overflow = (
                    unit_count > character_limit and context.is_atomic_span(start, end)
                )
                if unit_count <= character_limit or atomic_overflow:
                    best_end = end
                elif best_end is not None:
                    break
            end += 1
        if best_end is None:
            raise AssertionError("No safe lexical-word greedy boundary exists")
        end = best_end
        spans.append((start, end))
        start = end
    return spans


def _plan_region_dp(
    words: list[TimedToken],
    *,
    language: str,
    max_chars: int,
) -> list[tuple[int, int]]:
    context = _segmentation_context(words, language)
    return _plan_context_region_dp(
        context,
        0,
        len(words),
        max_chars=effective_max_chars(max_chars, language),
    )


def segment_tokens(
    tokens: list[TimedToken],
    *,
    language: str,
    max_chars: int,
    remove_gaps: bool = False,
    trim_end_punctuation: bool = False,
) -> list[SubtitleBlock]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not tokens:
        return []

    character_limit = effective_max_chars(max_chars, language)
    ordered = list(tokens)
    for previous, current in zip(ordered, ordered[1:]):
        if current.start + 1e-9 < previous.start:
            raise ValueError(
                "TimedToken start times must be monotonic; segmentation must not "
                "reorder model-provided text"
            )
    context = _segmentation_context(ordered, language)
    blocks: list[SubtitleBlock] = []
    atomic_overflow_intervals: set[tuple[float, float]] = set()
    start_index = 0

    while start_index < len(ordered):
        end_index = _planning_region_end(
            context,
            start_index,
            max_chars=character_limit,
        )
        planned_spans = _plan_context_region_dp(
            context,
            start_index,
            end_index,
            max_chars=character_limit,
        )
        for global_start, global_end in planned_spans:
            selected = ordered[global_start:global_end]
            text = join_token_text(selected, language)
            if text:
                block = SubtitleBlock(selected[0].start, selected[-1].end, text)
                blocks.append(block)
                if display_units(text) > character_limit:
                    if not context.is_atomic_span(global_start, global_end):
                        raise AssertionError(
                            "Subtitle text exceeded max_chars at a legal token boundary"
                        )
                    atomic_overflow_intervals.add((block.start, block.end))
        start_index = end_index

    blocks = _coalesce_overlapping_blocks(
        blocks,
        language=language,
        max_chars=character_limit,
    )

    blocks = postprocess_subtitle_blocks(
        blocks,
        remove_gaps=False,
        trim_end_punctuation=trim_end_punctuation,
    )

    illegal_overflows = [
        block
        for block in blocks
        if (
            display_units(block.text) > character_limit
            and (block.start, block.end) not in atomic_overflow_intervals
        )
    ]
    if illegal_overflows:
        raise AssertionError(
            "Subtitle text exceeded max_chars at a legal token boundary"
        )
    return postprocess_subtitle_blocks(blocks, remove_gaps=remove_gaps)
