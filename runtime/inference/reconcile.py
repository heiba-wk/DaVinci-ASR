from __future__ import annotations

from dataclasses import dataclass

from runtime.audio.chunker import InferenceWindow
from runtime.audio.vad import SpeechRegion, merge_speech_regions
from runtime.constants import (
    REGION_MERGE_GAP_SECONDS,
)
from runtime.core.types import TimedToken
from runtime.inference.aligner import AlignmentResult, normalize_alignment_text


@dataclass(slots=True)
class TokenCandidate:
    token: TimedToken
    window_index: int
    core_start: float
    core_end: float
    input_start: float
    input_end: float
    alignment_coverage: float


def build_token_candidates(
    window: InferenceWindow,
    alignment: AlignmentResult,
    *,
    sample_rate: int,
) -> list[TokenCandidate]:
    if not alignment.valid:
        return []
    core_start = window.core_start_sample / float(sample_rate)
    core_end = window.core_end_sample / float(sample_rate)
    candidates: list[TokenCandidate] = []
    for token in alignment.tokens:
        if token.end <= core_start or token.start >= core_end:
            continue
        candidates.append(
            TokenCandidate(
                token=TimedToken(token.text, token.start, token.end),
                window_index=window.index,
                core_start=core_start,
                core_end=core_end,
                input_start=window.input_start_sample / float(sample_rate),
                input_end=window.input_end_sample / float(sample_rate),
                alignment_coverage=alignment.text_coverage,
            )
        )
    return candidates


def _edge_distance(candidate: TokenCandidate) -> float:
    return max(
        0.0,
        min(
            candidate.token.start - candidate.input_start,
            candidate.input_end - candidate.token.end,
        ),
    )


def _same_occurrence(left: TokenCandidate, right: TokenCandidate) -> bool:
    if left.window_index == right.window_index:
        return False
    if normalize_alignment_text(left.token.text) != normalize_alignment_text(
        right.token.text
    ):
        return False
    overlap = min(left.token.end, right.token.end) - max(
        left.token.start, right.token.start
    )
    shorter = min(
        left.token.end - left.token.start,
        right.token.end - right.token.start,
    )
    return overlap > 0 and shorter > 0 and overlap / shorter >= 0.5


def _preference(candidate: TokenCandidate) -> tuple[float, float, int]:
    return (
        round(_edge_distance(candidate), 6),
        round(float(candidate.alignment_coverage), 6),
        -int(candidate.window_index),
    )


def reconcile_candidates(
    candidates: list[TokenCandidate],
) -> tuple[list[TimedToken], int]:
    ordered = sorted(
        enumerate(candidates),
        key=lambda item: (
            item[1].token.start,
            item[1].token.end,
            item[1].window_index,
            item[0],
        ),
    )
    kept: list[tuple[int, TokenCandidate]] = []
    most_recent_by_text: dict[str, int] = {}
    duplicate_count = 0

    for ordinal, candidate in ordered:
        normalized = normalize_alignment_text(candidate.token.text)
        previous_index = most_recent_by_text.get(normalized) if normalized else None
        if previous_index is not None and _same_occurrence(
            kept[previous_index][1], candidate
        ):
            duplicate_count += 1
            if _preference(candidate) > _preference(kept[previous_index][1]):
                # Retain the selected candidate's original occurrence ordinal.
                # Replacing only the earlier list slot can otherwise move a
                # later decoder unit ahead of its same-timestamp neighbour.
                kept[previous_index] = (ordinal, candidate)
            continue
        kept.append((ordinal, candidate))
        if normalized:
            most_recent_by_text[normalized] = len(kept) - 1

    tokens = [
        TimedToken(candidate.token.text, candidate.token.start, candidate.token.end)
        for _, candidate in sorted(
            kept,
            key=lambda item: (
                item[1].token.start,
                item[1].token.end,
                item[1].window_index,
                item[0],
            ),
        )
    ]
    return tokens, duplicate_count


def merge_regions(
    regions: list[SpeechRegion],
    *,
    total_samples: int,
    sample_rate: int,
    minimum_seconds: float = 0.0,
) -> list[SpeechRegion]:
    return merge_speech_regions(
        regions,
        total_samples=total_samples,
        merge_gap_samples=max(0, int(round(REGION_MERGE_GAP_SECONDS * sample_rate))),
        minimum_samples=max(1, int(round(minimum_seconds * sample_rate))),
    )


def intersect_speech_regions(
    speech_regions: list[SpeechRegion],
    region: SpeechRegion,
) -> list[SpeechRegion]:
    intersections: list[SpeechRegion] = []
    for speech in sorted(
        speech_regions, key=lambda item: (item.start_sample, item.end_sample)
    ):
        if speech.end_sample <= region.start_sample:
            continue
        if speech.start_sample >= region.end_sample:
            break
        start = max(speech.start_sample, region.start_sample)
        end = min(speech.end_sample, region.end_sample)
        if end > start:
            intersections.append(
                SpeechRegion(start, end, speech.confidence)
            )
    return intersections


def split_speech_regions(
    regions: list[SpeechRegion],
    *,
    boundaries: list[int],
    max_samples: int,
) -> list[SpeechRegion]:
    if max_samples <= 0:
        raise ValueError("Auditable speech span must be positive")
    ordered_boundaries = sorted(set(int(value) for value in boundaries))
    split: list[SpeechRegion] = []
    for region in sorted(
        regions, key=lambda item: (item.start_sample, item.end_sample)
    ):
        points = [region.start_sample]
        points.extend(
            value
            for value in ordered_boundaries
            if region.start_sample < value < region.end_sample
        )
        points.append(region.end_sample)
        for span_start, span_end in zip(points, points[1:]):
            start = span_start
            while start < span_end:
                end = min(span_end, start + max_samples)
                split.append(SpeechRegion(start, end, region.confidence))
                start = end
    return split


def find_unclaimed_speech_regions(
    speech_regions: list[SpeechRegion],
    candidates: list[TokenCandidate],
    sample_rate: int,
) -> list[SpeechRegion]:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    claimed: list[tuple[int, int]] = []
    for candidate in candidates:
        if not normalize_alignment_text(candidate.token.text):
            continue
        start = max(
            int(round(candidate.core_start * sample_rate)),
            int(round(candidate.token.start * sample_rate)),
        )
        end = min(
            int(round(candidate.core_end * sample_rate)),
            int(round(candidate.token.end * sample_rate)),
        )
        if end > start:
            claimed.append((start, end))
    claimed.sort()
    return [
        region
        for region in speech_regions
        if not any(
            start < region.end_sample and end > region.start_sample
            for start, end in claimed
        )
    ]


def split_recovery_regions(
    regions: list[SpeechRegion],
    *,
    max_samples: int,
) -> list[SpeechRegion]:
    if max_samples <= 0:
        raise ValueError("Recovery core size must be positive")
    split: list[SpeechRegion] = []
    for region in sorted(
        regions, key=lambda item: (item.start_sample, item.end_sample)
    ):
        start = region.start_sample
        while start < region.end_sample:
            end = min(region.end_sample, start + max_samples)
            split.append(SpeechRegion(start, end, region.confidence))
            start = end
    return split


def speech_samples_in_region(
    speech_regions: list[SpeechRegion], region: SpeechRegion
) -> int:
    return sum(
        max(
            0,
            min(item.end_sample, region.end_sample)
            - max(item.start_sample, region.start_sample),
        )
        for item in speech_regions
    )
