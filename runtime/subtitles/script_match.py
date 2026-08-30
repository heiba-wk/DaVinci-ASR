from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from runtime.core.types import SubtitleBlock, TimedToken
from runtime.inference.aligner import AlignmentResult, normalize_alignment_text
from runtime.subtitles.segmenter import display_units

DIRECT_MAX_AUDIO_SECONDS = 300.0
DIRECT_MIN_ALIGNMENT_COVERAGE = 0.98
DIRECT_MIN_MAPPING_COVERAGE = 0.98
DIRECT_MIN_LINE_COVERAGE = 0.90
DIRECT_MIN_UNIQUE_INTERVAL_RATIO = 0.80
DIRECT_MIN_ALIGNED_SPAN_RATIO = 0.35
DIRECT_LOCAL_COLLAPSE_MAX_SECONDS = 0.50
DIRECT_LOCAL_COLLAPSE_MIN_DISPLAY_UNITS_PER_SECOND = 24.0
FALLBACK_MIN_ANCHOR_COVERAGE = 0.35
FALLBACK_MIN_GLOBAL_ANCHOR_COVERAGE = 0.50
FALLBACK_MIN_ALIGNMENT_COVERAGE = 0.95
FALLBACK_MIN_MAPPING_COVERAGE = 0.95
FALLBACK_WINDOW_MIN_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class ScriptLine:
    index: int
    line_id: str
    source_line_number: int
    text: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class _ReferenceUnit:
    text: str
    line_index: int
    group_index: int


@dataclass(frozen=True, slots=True)
class _TimedUnit:
    text: str
    start: float
    end: float


@dataclass(slots=True)
class ScriptMapping:
    blocks: list[SubtitleBlock]
    mapping_coverage: float
    line_coverages: list[float]
    unmapped_line_indices: list[int]
    reference_character_count: int
    matched_character_count: int


@dataclass(frozen=True, slots=True)
class IntervalDiagnostics:
    invalid_interval_count: int
    non_monotonic_interval_count: int
    overlap_count: int
    out_of_bounds_count: int


@dataclass(frozen=True, slots=True)
class DirectQuality:
    passed: bool
    reasons: tuple[str, ...]
    unique_interval_ratio: float
    aligned_span_ratio: float
    intervals: IntervalDiagnostics
    locally_collapsed_line_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AnchorWindow:
    line: ScriptLine
    start: float
    end: float
    anchor_coverage: float


@dataclass(slots=True)
class AnchorPlan:
    windows: list[AnchorWindow]
    mapping_coverage: float
    line_coverages: list[float]
    unmapped_line_indices: list[int]


def parse_reference_lines(reference_text: str) -> list[ScriptLine]:
    lines: list[ScriptLine] = []
    for source_line_number, raw_line in enumerate(str(reference_text).splitlines(), 1):
        if not raw_line.strip():
            continue
        index = len(lines)
        lines.append(
            ScriptLine(
                index=index,
                line_id=f"line_{index + 1:06d}",
                source_line_number=source_line_number,
                text=raw_line,
                normalized_text=normalize_alignment_text(raw_line),
            )
        )
    if not lines:
        raise ValueError("SCRIPT_MATCH_REFERENCE_EMPTY")
    return lines


def reference_alignment_text(lines: list[ScriptLine]) -> str:
    return "\n".join(line.text for line in lines)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _word_mapping_groups(value: str) -> list[str]:
    import unicodedata

    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    groups: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            groups.append("".join(current))
            current.clear()

    for index, character in enumerate(normalized):
        unit = normalize_alignment_text(character)
        if unit:
            if _is_cjk(character):
                flush()
                groups.append(unit)
            else:
                current.append(unit)
            continue
        previous_alnum = index > 0 and normalize_alignment_text(normalized[index - 1])
        following_alnum = index + 1 < len(normalized) and normalize_alignment_text(
            normalized[index + 1]
        )
        if character in {"'", "’", ".", "-"} and previous_alnum and following_alnum:
            continue
        flush()
    flush()
    return groups


def _mapping_groups(value: str, language: str) -> list[str]:
    normalized = normalize_alignment_text(value)
    if language in {"Chinese", "Cantonese"}:
        return list(normalized)
    return _word_mapping_groups(value)


def _reference_units(lines: list[ScriptLine], language: str) -> list[_ReferenceUnit]:
    units: list[_ReferenceUnit] = []
    group_index = 0
    for line in lines:
        for group in _mapping_groups(line.text, language):
            units.extend(
                _ReferenceUnit(character, line.index, group_index)
                for character in group
            )
            group_index += 1
    return units


def _group_coverage(
    reference_units: list[_ReferenceUnit],
    matched_reference_indices: set[int],
    *,
    line_count: int,
) -> tuple[float, list[float]]:
    group_totals: dict[int, int] = {}
    group_matches: dict[int, int] = {}
    group_lines: dict[int, int] = {}
    for index, unit in enumerate(reference_units):
        group_totals[unit.group_index] = group_totals.get(unit.group_index, 0) + 1
        group_lines[unit.group_index] = unit.line_index
        if index in matched_reference_indices:
            group_matches[unit.group_index] = group_matches.get(unit.group_index, 0) + 1
    complete = {
        group_index
        for group_index, total in group_totals.items()
        if group_matches.get(group_index, 0) == total
    }
    totals_by_line = [0 for _index in range(line_count)]
    matches_by_line = [0 for _index in range(line_count)]
    for group_index, line_index in group_lines.items():
        totals_by_line[line_index] += 1
        if group_index in complete:
            matches_by_line[line_index] += 1
    line_coverages = [
        matches / total if total else 0.0
        for matches, total in zip(matches_by_line, totals_by_line)
    ]
    return (
        len(complete) / len(group_totals) if group_totals else 0.0,
        line_coverages,
    )


def _token_units(tokens: list[TimedToken]) -> list[_TimedUnit]:
    units: list[_TimedUnit] = []
    for token in tokens:
        normalized = normalize_alignment_text(token.text)
        if not normalized:
            continue
        duration = token.end - token.start
        count = len(normalized)
        for index, character in enumerate(normalized):
            units.append(
                _TimedUnit(
                    character,
                    token.start + duration * index / count,
                    token.start + duration * (index + 1) / count,
                )
            )
    return units


def _matching_pairs(expected: str, actual: str) -> list[tuple[int, int]]:
    if expected == actual:
        return [(index, index) for index in range(len(expected))]
    matcher = SequenceMatcher(None, expected, actual, autojunk=False)
    pairs: list[tuple[int, int]] = []
    for match in matcher.get_matching_blocks():
        pairs.extend(
            (match.a + offset, match.b + offset) for offset in range(match.size)
        )
    return pairs


def _stabilize_blocks(blocks: list[SubtitleBlock]) -> list[SubtitleBlock]:
    output = [SubtitleBlock(block.start, block.end, block.text) for block in blocks]
    for index in range(1, len(output)):
        previous = output[index - 1]
        current = output[index]
        if current.start + 1e-9 >= previous.end:
            continue
        lower = previous.start + 1e-6
        upper = current.end - 1e-6
        if lower >= upper:
            continue
        boundary = min(max((previous.end + current.start) / 2.0, lower), upper)
        previous.end = boundary
        current.start = boundary
    return output


def map_aligned_tokens_to_lines(
    tokens: list[TimedToken],
    lines: list[ScriptLine],
    *,
    language: str,
    minimum_line_coverage: float = DIRECT_MIN_LINE_COVERAGE,
) -> ScriptMapping:
    if not 0.0 <= minimum_line_coverage <= 1.0:
        raise ValueError("minimum_line_coverage must be between 0 and 1")
    reference_units = _reference_units(lines, language)
    timed_units = _token_units(tokens)
    expected = "".join(unit.text for unit in reference_units)
    actual = "".join(unit.text for unit in timed_units)
    pairs = _matching_pairs(expected, actual)
    matched_by_line: list[list[_TimedUnit]] = [[] for _line in lines]
    matched_reference_indices: set[int] = set()
    for expected_index, actual_index in pairs:
        if expected_index >= len(reference_units) or actual_index >= len(timed_units):
            continue
        line_index = reference_units[expected_index].line_index
        matched_by_line[line_index].append(timed_units[actual_index])
        matched_reference_indices.add(expected_index)

    mapping_coverage, line_coverages = _group_coverage(
        reference_units,
        matched_reference_indices,
        line_count=len(lines),
    )
    unmapped: list[int] = []
    blocks: list[SubtitleBlock] = []
    for line in lines:
        coverage = line_coverages[line.index]
        matches = matched_by_line[line.index]
        if coverage + 1e-9 < minimum_line_coverage or not matches:
            unmapped.append(line.index)
            continue
        start = min(unit.start for unit in matches)
        end = max(unit.end for unit in matches)
        if end <= start:
            unmapped.append(line.index)
            continue
        blocks.append(SubtitleBlock(start, end, line.text))

    matched = len(pairs)
    total = len(reference_units)
    return ScriptMapping(
        blocks=_stabilize_blocks(blocks),
        mapping_coverage=mapping_coverage,
        line_coverages=line_coverages,
        unmapped_line_indices=unmapped,
        reference_character_count=total,
        matched_character_count=matched,
    )


def interval_diagnostics(
    blocks: list[SubtitleBlock], duration: float
) -> IntervalDiagnostics:
    invalid = 0
    non_monotonic = 0
    overlaps = 0
    out_of_bounds = 0
    previous: SubtitleBlock | None = None
    for block in blocks:
        if block.start < 0.0 or block.end <= block.start:
            invalid += 1
        if block.start < -1e-6 or block.end > duration + 1e-6:
            out_of_bounds += 1
        if previous is not None:
            if block.start + 1e-6 < previous.start or block.end + 1e-6 < previous.end:
                non_monotonic += 1
            if block.start + 1e-6 < previous.end:
                overlaps += 1
        previous = block
    return IntervalDiagnostics(invalid, non_monotonic, overlaps, out_of_bounds)


def evaluate_direct_quality(
    alignment: AlignmentResult,
    mapping: ScriptMapping,
    *,
    line_count: int,
    duration: float,
) -> DirectQuality:
    reasons: list[str] = []
    if not alignment.valid:
        reasons.append(alignment.error or "ALIGNMENT_INVALID")
    if alignment.text_coverage + 1e-9 < DIRECT_MIN_ALIGNMENT_COVERAGE:
        reasons.append("LOW_ALIGNMENT_COVERAGE")
    if mapping.mapping_coverage + 1e-9 < DIRECT_MIN_MAPPING_COVERAGE:
        reasons.append("LOW_SCRIPT_MAPPING_COVERAGE")
    if mapping.unmapped_line_indices or len(mapping.blocks) != line_count:
        reasons.append("UNMAPPED_SCRIPT_LINES")

    interval_count = len(
        {(round(token.start, 6), round(token.end, 6)) for token in alignment.tokens}
    )
    unique_ratio = interval_count / len(alignment.tokens) if alignment.tokens else 0.0
    if (
        len(alignment.tokens) >= 10
        and unique_ratio + 1e-9 < DIRECT_MIN_UNIQUE_INTERVAL_RATIO
    ):
        reasons.append("COLLAPSED_ALIGNMENT_INTERVALS")

    if alignment.tokens and duration > 0.0:
        span = max(token.end for token in alignment.tokens) - min(
            token.start for token in alignment.tokens
        )
        span_ratio = max(0.0, span) / duration
    else:
        span_ratio = 0.0
    if (
        duration >= 10.0
        and mapping.reference_character_count >= 10
        and span_ratio + 1e-9 < DIRECT_MIN_ALIGNED_SPAN_RATIO
    ):
        reasons.append("COMPRESSED_ALIGNMENT_SPAN")

    locally_collapsed = tuple(
        index
        for index, block in enumerate(mapping.blocks)
        if (
            block.end - block.start < DIRECT_LOCAL_COLLAPSE_MAX_SECONDS
            and display_units(block.text) / (block.end - block.start)
            > DIRECT_LOCAL_COLLAPSE_MIN_DISPLAY_UNITS_PER_SECOND
        )
    )
    if locally_collapsed:
        # Global coverage and interval ratios can still look healthy when one
        # decoder region collapses many words onto a single timestamp bin.
        # A Script Match result is only as readable as its worst preserved
        # line, so route this local failure through the existing ASR-anchor
        # fallback instead of accepting an implausibly short subtitle.
        reasons.append("LOCALLY_COLLAPSED_SCRIPT_INTERVALS")

    intervals = interval_diagnostics(mapping.blocks, duration)
    if intervals.invalid_interval_count:
        reasons.append("INVALID_SCRIPT_INTERVALS")
    if intervals.non_monotonic_interval_count:
        reasons.append("NON_MONOTONIC_SCRIPT_INTERVALS")
    if intervals.overlap_count:
        reasons.append("OVERLAPPING_SCRIPT_INTERVALS")
    if intervals.out_of_bounds_count:
        reasons.append("SCRIPT_INTERVAL_OUT_OF_BOUNDS")
    return DirectQuality(
        passed=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        unique_interval_ratio=unique_ratio,
        aligned_span_ratio=span_ratio,
        intervals=intervals,
        locally_collapsed_line_indices=locally_collapsed,
    )


def _block_units(blocks: list[SubtitleBlock]) -> list[_TimedUnit]:
    units: list[_TimedUnit] = []
    for block in blocks:
        normalized = normalize_alignment_text(block.text)
        if not normalized:
            continue
        duration = block.end - block.start
        count = len(normalized)
        for index, character in enumerate(normalized):
            units.append(
                _TimedUnit(
                    character,
                    block.start + duration * index / count,
                    block.start + duration * (index + 1) / count,
                )
            )
    return units


def build_anchor_plan(
    lines: list[ScriptLine],
    anchor_blocks: list[SubtitleBlock],
    *,
    language: str,
    duration: float,
    minimum_line_coverage: float = FALLBACK_MIN_ANCHOR_COVERAGE,
) -> AnchorPlan:
    if duration <= 0.0:
        raise ValueError("SCRIPT_MATCH_AUDIO_EMPTY")
    reference_units = _reference_units(lines, language)
    timed_units = _block_units(anchor_blocks)
    expected = "".join(unit.text for unit in reference_units)
    actual = "".join(unit.text for unit in timed_units)
    pairs = _matching_pairs(expected, actual)
    matched_by_line: list[list[_TimedUnit]] = [[] for _line in lines]
    matched_reference_indices: set[int] = set()
    for expected_index, actual_index in pairs:
        if expected_index >= len(reference_units) or actual_index >= len(timed_units):
            continue
        line_index = reference_units[expected_index].line_index
        matched_by_line[line_index].append(timed_units[actual_index])
        matched_reference_indices.add(expected_index)

    mapping_coverage, line_coverages = _group_coverage(
        reference_units,
        matched_reference_indices,
        line_count=len(lines),
    )
    unmapped: list[int] = []
    raw_ranges: list[tuple[float, float]] = []
    for line in lines:
        coverage = line_coverages[line.index]
        matches = matched_by_line[line.index]
        if coverage + 1e-9 < minimum_line_coverage or not matches:
            unmapped.append(line.index)
            raw_ranges.append((0.0, 0.0))
            continue
        raw_ranges.append(
            (
                max(0.0, min(unit.start for unit in matches)),
                min(duration, max(unit.end for unit in matches)),
            )
        )

    windows: list[AnchorWindow] = []
    if not unmapped:
        boundaries = [0.0]
        for index in range(len(lines) - 1):
            candidate = (raw_ranges[index][1] + raw_ranges[index + 1][0]) / 2.0
            minimum = boundaries[-1] + FALLBACK_WINDOW_MIN_SECONDS
            remaining = len(lines) - index - 1
            maximum = duration - remaining * FALLBACK_WINDOW_MIN_SECONDS
            boundaries.append(min(max(candidate, minimum), maximum))
        boundaries.append(duration)
        for index, line in enumerate(lines):
            windows.append(
                AnchorWindow(
                    line=line,
                    start=boundaries[index],
                    end=boundaries[index + 1],
                    anchor_coverage=line_coverages[index],
                )
            )

    return AnchorPlan(
        windows=windows,
        mapping_coverage=mapping_coverage,
        line_coverages=line_coverages,
        unmapped_line_indices=unmapped,
    )
