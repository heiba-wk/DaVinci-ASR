from __future__ import annotations

import gc
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

from runtime.constants import (
    ALIGNMENT_BOUNDARY_MAX_TIMESTAMP_BINS,
    ALIGNMENT_LANGUAGES,
    ALIGNMENT_MIN_TEXT_COVERAGE,
    SAMPLE_RATE,
)
from runtime.core.hardware import release_accelerator_cache, torch_dtype
from runtime.core.types import HardwareProfile, TimedToken


@dataclass(slots=True)
class AlignmentResult:
    tokens: list[TimedToken]
    text_coverage: float
    valid: bool
    error: str = ""


def normalize_alignment_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character).startswith(("L", "N"))
    )


def alignment_text_coverage(transcript: str, tokens: list[TimedToken]) -> float:
    expected = normalize_alignment_text(transcript)
    actual = normalize_alignment_text("".join(token.text for token in tokens))
    if not expected:
        return 1.0 if not actual else 0.0
    if not actual:
        return 0.0
    return float(SequenceMatcher(None, expected, actual, autojunk=False).ratio())


def _alignment_boundary_tolerance_seconds(processor: object) -> float:
    timestamp_segment_ms = float(getattr(processor, "timestamp_segment_time"))
    if timestamp_segment_ms <= 0.0:
        raise RuntimeError("Invalid ForcedAligner timestamp segment duration")
    return (
        timestamp_segment_ms * ALIGNMENT_BOUNDARY_MAX_TIMESTAMP_BINS / 1000.0
    )


def validate_alignment(
    transcript: str,
    tokens: list[TimedToken],
    *,
    input_start: float,
    input_end: float,
    minimum_coverage: float = ALIGNMENT_MIN_TEXT_COVERAGE,
) -> AlignmentResult:
    if input_start < 0 or input_end <= input_start:
        raise ValueError("Invalid alignment input interval")
    if transcript.strip() and not tokens:
        return AlignmentResult([], 0.0, False, "EMPTY_ALIGNMENT")

    previous_start = input_start
    previous_end = input_start
    for index, token in enumerate(tokens):
        if token.start < input_start - 1e-6 or token.end > input_end + 1e-6:
            return AlignmentResult([], 0.0, False, f"TOKEN_OUT_OF_BOUNDS:{index}")
        if token.end <= token.start:
            return AlignmentResult([], 0.0, False, f"INVALID_INTERVAL:{index}")
        if index and (
            token.start + 1e-6 < previous_start
            or token.end + 1e-6 < previous_end
        ):
            return AlignmentResult([], 0.0, False, f"NON_MONOTONIC_TIME:{index}")
        previous_start = token.start
        previous_end = token.end

    coverage = alignment_text_coverage(transcript, tokens)
    if coverage + 1e-9 < minimum_coverage:
        return AlignmentResult([], coverage, False, "LOW_TEXT_COVERAGE")
    return AlignmentResult(list(tokens), coverage, True)


def _tokens_from_decoded_alignment(
    decoded: Sequence[Mapping[str, object]],
    *,
    offset_seconds: float,
    input_start: float,
    input_end: float,
    boundary_tolerance_seconds: float = 0.0,
) -> tuple[list[TimedToken], str]:
    """Preserve decoder units while borrowing only reliable time intervals."""
    if boundary_tolerance_seconds < 0.0:
        raise ValueError("Alignment boundary tolerance cannot be negative")
    tokens: list[TimedToken] = []
    pending_text: list[str] = []
    pending_first_index = -1
    previous_start = input_start
    previous_end = input_start

    final_index = len(decoded) - 1
    for index, item in enumerate(decoded):
        try:
            start = float(item["start_time"]) + offset_seconds
            end = float(item["end_time"]) + offset_seconds
        except (KeyError, TypeError, ValueError):
            return [], f"INVALID_TIMESTAMP:{index}"
        if start < input_start - 1e-6:
            if (
                index != 0
                or input_start - start > boundary_tolerance_seconds + 1e-6
            ):
                return [], f"TOKEN_OUT_OF_BOUNDS:{index}"
            start = input_start
        if end > input_end + 1e-6:
            if (
                index != final_index
                or end - input_end > boundary_tolerance_seconds + 1e-6
            ):
                return [], f"TOKEN_OUT_OF_BOUNDS:{index}"
            end = input_end
        if end + 1e-9 < start:
            return [], f"INVALID_INTERVAL:{index}"
        if index and (
            start + 1e-6 < previous_start
            or end + 1e-6 < previous_end
        ):
            return [], f"NON_MONOTONIC_TIME:{index}"
        previous_start = start
        previous_end = end
        text = str(item.get("text", ""))

        if end <= start + 1e-9:
            if not pending_text:
                pending_first_index = index
            pending_text.append(text)
            continue

        if pending_text:
            # The upstream decoder intentionally repairs timestamps to a
            # non-decreasing sequence, so a real text unit can retain only an
            # anchor. Preserve every decoder occurrence and let each one share
            # the following reliable interval. Rendering decides later whether
            # adjacent units need spaces; alignment repair must not join text.
            tokens.extend(
                TimedToken(pending, start, end) for pending in pending_text
            )
            pending_text.clear()
            pending_first_index = -1
        tokens.append(TimedToken(text, start, end))

    if pending_text:
        if not tokens:
            return [], f"INVALID_INTERVAL:{pending_first_index}"
        previous = tokens[-1]
        tokens.extend(
            TimedToken(pending, previous.start, previous.end)
            for pending in pending_text
        )

    if (
        len(tokens) >= 2
        and input_start > 0.0
        and abs(tokens[0].start - input_start) <= 1e-6
        and tokens[0].end - tokens[0].start <= 0.25
        and tokens[1].start - tokens[0].end >= 1.0
    ):
        # A repaired aligner outlier can become a short positive interval at
        # the arbitrary input boundary, far ahead of the next reliable unit.
        # Preserve the decoder occurrence but attach it to that reliable
        # interval, just as for a zero-duration anchor above.
        first = tokens[0]
        following = tokens[1]
        tokens[0] = TimedToken(
            first.text,
            following.start,
            following.end,
        )
    return tokens, ""


class QwenForcedAlignerEngine:
    def __init__(self, model_path: str | Path, hardware: HardwareProfile) -> None:
        self.model_path = Path(model_path)
        self.hardware = hardware
        self.processor: Any | None = None
        self.model: Any | None = None

    def load(self) -> None:
        from transformers import AutoModelForTokenClassification, AutoProcessor

        if self.model is not None:
            return
        dtype = torch_dtype(self.hardware)
        if self.processor is None:
            self.processor = AutoProcessor.from_pretrained(
                self.model_path, local_files_only=True
            )
        self.model = AutoModelForTokenClassification.from_pretrained(
            self.model_path,
            dtype=dtype,
            local_files_only=True,
        ).to(self.hardware.device)
        self.model.eval()

    def align_with_quality(
        self,
        waveform: np.ndarray,
        transcript: str,
        language: str,
        *,
        offset_seconds: float = 0.0,
        input_start_seconds: float | None = None,
        input_end_seconds: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AlignmentResult:
        import numpy as np
        import torch

        if language not in ALIGNMENT_LANGUAGES:
            raise ValueError(f"Forced alignment does not support {language}")
        if self.model is None or self.processor is None:
            raise RuntimeError("Forced aligner model is not loaded")
        if is_cancelled is not None and is_cancelled():
            raise InterruptedError("Job cancelled")
        audio = np.asarray(waveform, dtype=np.float32)
        if audio.ndim != 1:
            raise ValueError("Forced aligner expects mono audio")
        input_start = (
            float(offset_seconds)
            if input_start_seconds is None
            else float(input_start_seconds)
        )
        input_end = (
            float(offset_seconds) + len(audio) / float(SAMPLE_RATE)
            if input_end_seconds is None
            else float(input_end_seconds)
        )

        # The optional Korean morphology dependency is GPLv3. The native
        # Unicode/whitespace path produces Korean eojeol units without it.
        processor_language = None if language == "Korean" else language
        inputs, word_lists = self.processor.prepare_forced_aligner_inputs(
            audio=audio,
            transcript=transcript,
            language=processor_language,
        )
        inputs = inputs.to(self.model.device, self.model.dtype)
        with torch.inference_mode():
            outputs = self.model(**inputs)
        if is_cancelled is not None and is_cancelled():
            raise InterruptedError("Job cancelled")
        decoded_batches = self.processor.decode_forced_alignment(
            logits=outputs.logits,
            input_ids=inputs["input_ids"],
            word_lists=word_lists,
            timestamp_token_id=self.model.config.timestamp_token_id,
        )
        if not isinstance(decoded_batches, (list, tuple)) or not decoded_batches:
            return AlignmentResult([], 0.0, False, "EMPTY_ALIGNMENT")
        decoded = decoded_batches[0]
        if not isinstance(decoded, (list, tuple)):
            return AlignmentResult([], 0.0, False, "INVALID_ALIGNMENT_OUTPUT")

        tokens, decode_error = _tokens_from_decoded_alignment(
            decoded,
            offset_seconds=offset_seconds,
            input_start=input_start,
            input_end=input_end,
            boundary_tolerance_seconds=_alignment_boundary_tolerance_seconds(
                self.processor
            ),
        )
        if decode_error:
            return AlignmentResult([], 0.0, False, decode_error)
        return validate_alignment(
            transcript,
            tokens,
            input_start=input_start,
            input_end=input_end,
        )

    def align(
        self,
        waveform: np.ndarray,
        transcript: str,
        language: str,
        *,
        offset_seconds: float = 0.0,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[TimedToken]:
        result = self.align_with_quality(
            waveform,
            transcript,
            language,
            offset_seconds=offset_seconds,
            is_cancelled=is_cancelled,
        )
        if not result.valid:
            raise RuntimeError(f"ALIGNMENT_INVALID: {result.error}")
        return result.tokens

    def release_model(self) -> None:
        self.model = None
        gc.collect()
        release_accelerator_cache(self.hardware)

    def unload(self) -> None:
        self.release_model()
        self.processor = None
