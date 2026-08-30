from __future__ import annotations

import json
import logging
import shutil
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from runtime.audio.chunker import InferenceWindow, plan_windows
from runtime.audio.store import AudioStore, prepare_audio_store
from runtime.audio.vad import SileroVAD, SpeechRegion
from runtime.constants import (
    AUTO_SUBTITLE_MODE,
    ALIGNMENT_LANGUAGES,
    ASR_WINDOW_MAX_NEW_TOKENS,
    PROTOCOL_VERSION,
    RECOVERY_CONTEXT_SECONDS,
    RECOVERY_CORE_MAX_SECONDS,
    SCRIPT_MATCH_MODE,
    UNSUPPORTED_ALIGNMENT_ERROR,
    VAD_MIN_SPEECH_MS,
    WINDOW_CONTEXT_SECONDS,
    WINDOW_HARD_MAX_SECONDS,
    WINDOW_MIN_CORE_SECONDS,
    WINDOW_SOFT_MAX_SECONDS,
    WINDOW_TARGET_SECONDS,
)
from runtime.core.paths import RuntimePaths
from runtime.core.performance import PerformanceMetrics
from runtime.core.types import (
    HardwareProfile,
    JobRequest,
    JobResult,
    SubtitleBlock,
    SubtitleOptions,
    TimedToken,
    TimelineSpec,
)
from runtime.inference.aligner import AlignmentResult, QwenForcedAlignerEngine
from runtime.inference.asr import QwenASREngine
from runtime.inference.model_manager import ModelManager
from runtime.inference.reconcile import (
    TokenCandidate,
    build_token_candidates,
    find_unclaimed_speech_regions,
    intersect_speech_regions,
    merge_regions,
    reconcile_candidates,
    speech_samples_in_region,
    split_recovery_regions,
    split_speech_regions,
)
from runtime.inference.service import InferenceService
from runtime.ipc.atomic import atomic_write_json_fast
from runtime.subtitles.punctuation import restore_punctuation
from runtime.subtitles.quantize import quantize_blocks
from runtime.subtitles.script_match import (
    DIRECT_MAX_AUDIO_SECONDS,
    DIRECT_MIN_LINE_COVERAGE,
    FALLBACK_MIN_ALIGNMENT_COVERAGE,
    FALLBACK_MIN_GLOBAL_ANCHOR_COVERAGE,
    FALLBACK_MIN_MAPPING_COVERAGE,
    ScriptLine,
    ScriptMapping,
    build_anchor_plan,
    evaluate_direct_quality,
    interval_diagnostics,
    map_aligned_tokens_to_lines,
    parse_reference_lines,
    reference_alignment_text,
)
from runtime.subtitles.segmenter import (
    effective_max_chars,
    join_token_text,
    postprocess_subtitle_blocks,
    segment_tokens,
)
from runtime.subtitles.srt import write_srt

StatusCallback = Callable[[str, int, str], None]
CancelCallback = Callable[[], bool]
ABNORMAL_PROTOCOL_MARKERS = (
    "<|audio|>",
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
)
PREVIOUS_TAIL_CHARACTERS = 160
CPU_SELF_REPAIR_ERRORS = frozenset(
    {"ASR_TOKEN_LIMIT_REACHED", "ASR_REPETITION_DETECTED"}
)


class SpeechDetector(Protocol):
    def detect(
        self,
        store: AudioStore,
        *,
        is_cancelled: CancelCallback | None = None,
    ) -> list[SpeechRegion]: ...


class ASREngine(Protocol):
    def load(self) -> None: ...

    def transcribe(
        self,
        waveform: Any,
        *,
        language: str | None,
        prompt: str = "",
        max_new_tokens: int | None = None,
        is_cancelled: CancelCallback | None = None,
    ) -> Mapping[str, object]: ...

    def unload(self) -> None: ...


class AlignerEngine(Protocol):
    def align_with_quality(
        self,
        waveform: Any,
        transcript: str,
        language: str,
        *,
        offset_seconds: float = 0.0,
        input_start_seconds: float | None = None,
        input_end_seconds: float | None = None,
        is_cancelled: CancelCallback | None = None,
    ) -> AlignmentResult: ...


@dataclass(slots=True)
class ASRResult:
    valid: bool
    language: str
    text: str
    generated_tokens: int
    max_new_tokens: int
    attempt_count: int
    error: str = ""

    def diagnostic(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "language": self.language,
            "generated_tokens": self.generated_tokens,
            "max_new_tokens": self.max_new_tokens,
            "attempt_count": self.attempt_count,
            "error": self.error,
        }


@dataclass(slots=True)
class LanguageState:
    requested: str
    trusted_language: str = ""

    @property
    def automatic(self) -> bool:
        return self.requested == "Auto"

    @property
    def inference_language(self) -> str | None:
        if not self.automatic:
            return self.requested
        return self.trusted_language or None

    @property
    def trusted_or_requested(self) -> str:
        return self.trusted_language if self.automatic else self.requested

    def accept(self, language: str, text: str) -> str:
        if not self.automatic:
            return self.requested
        if self.trusted_language:
            return self.trusted_language
        if not text.strip():
            return ""
        detected = str(language).strip()
        if detected not in ALIGNMENT_LANGUAGES:
            raise ValueError(UNSUPPORTED_ALIGNMENT_ERROR)
        self.trusted_language = detected
        return detected


@dataclass(slots=True)
class WindowRecord:
    window: InferenceWindow
    asr: ASRResult
    recovery: bool = False
    alignment: AlignmentResult | None = None
    self_repair_backend: str = ""
    self_repair_from_error: str = ""
    core_claimed: bool = False

    def diagnostic(self, sample_rate: int) -> dict[str, object]:
        value = self.window.to_dict(sample_rate)
        value["recovery"] = self.recovery
        value["asr"] = self.asr.diagnostic()
        value["core_claimed"] = self.core_claimed
        if self.alignment is not None:
            value["alignment_valid"] = self.alignment.valid
            value["alignment_text_coverage"] = round(self.alignment.text_coverage, 4)
            value["alignment_error"] = self.alignment.error
        if self.self_repair_backend:
            value["self_repair_backend"] = self.self_repair_backend
            value["self_repair_from_error"] = self.self_repair_from_error
        return value


@dataclass(slots=True)
class PipelineStats:
    asr_inference_seconds: float = 0.0
    aligner_inference_seconds: float = 0.0
    asr_attempt_count: int = 0
    asr_token_limit_count: int = 0
    asr_repetition_stop_count: int = 0
    alignment_invalid_count: int = 0
    recovery_success_count: int = 0
    cpu_self_repair_attempt_count: int = 0
    cpu_self_repair_success_count: int = 0


def _accumulate_metric_ms(
    metrics: PerformanceMetrics, key: str, milliseconds: object
) -> None:
    try:
        value = max(0.0, float(milliseconds))
    except (TypeError, ValueError):
        return
    metrics.values[key] = round(float(metrics.values.get(key, 0.0)) + value, 3)


class _ASRProtocolError(RuntimeError):
    pass


def build_asr_prompt(user_prompt: str, previous_tail: str) -> str:
    user = str(user_prompt or "").strip()
    tail = str(previous_tail or "").strip()[-PREVIOUS_TAIL_CHARACTERS:]
    if not tail:
        return user
    context = f"Previous accepted transcript tail: {tail}"
    return f"{user}\n{context}" if user else context


def _is_abnormal_text(text: str) -> bool:
    value = text.strip()
    folded = value.casefold().strip(" :.!。！")
    if folded in {"language", "transcription", "transcript", "text"}:
        return True
    if any(marker in value for marker in ABNORMAL_PROTOCOL_MARKERS):
        return True
    if len(value) >= 128:
        characters = [character for character in value if not character.isspace()]
        if (
            characters
            and Counter(characters).most_common(1)[0][1] / len(characters) >= 0.90
        ):
            return True
        words = value.casefold().split()
        if (
            len(words) >= 16
            and Counter(words).most_common(1)[0][1] / len(words) >= 0.80
        ):
            return True
    return False


def _is_backend_runtime_error(exc: BaseException) -> bool:
    if isinstance(exc, MemoryError):
        return True
    value = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        marker in value
        for marker in (
            "outofmemory",
            "out of memory",
            "mps backend",
            "mps runtime",
            "metal command buffer",
            "cuda error",
            "cudnn",
            "cublas",
        )
    )


def _parse_asr_response(
    value: object,
    *,
    requested_budget: int,
) -> tuple[str, str, int, int, bool, bool]:
    if not isinstance(value, Mapping) or value.get("protocol_ok", True) is not True:
        raise _ASRProtocolError("ASR_PROTOCOL_PARSE_FAILED")
    raw_language = value.get("language", "")
    raw_text = value.get("text", "")
    language = "" if raw_language is None else str(raw_language).strip()
    text = "" if raw_text is None else str(raw_text).strip()
    try:
        generated_tokens = int(value.get("generated_tokens", 0))
        reported_budget = int(value.get("max_new_tokens", requested_budget))
    except (TypeError, ValueError) as exc:
        raise _ASRProtocolError("ASR_DIAGNOSTIC_PARSE_FAILED") from exc
    if generated_tokens < 0 or reported_budget <= 0:
        raise _ASRProtocolError("ASR_DIAGNOSTIC_PARSE_FAILED")
    ended_normally = bool(value.get("ended_normally", False))
    hit_token_limit = generated_tokens >= requested_budget and not ended_normally
    repetition_detected = bool(value.get("repetition_detected", False))
    return (
        language,
        text,
        generated_tokens,
        reported_budget,
        hit_token_limit,
        repetition_detected,
    )


def _record_asr_result(stats: PipelineStats, result: ASRResult) -> None:
    stats.asr_attempt_count += result.attempt_count
    if result.error == "ASR_TOKEN_LIMIT_REACHED":
        stats.asr_token_limit_count += 1
    elif result.error == "ASR_REPETITION_DETECTED":
        stats.asr_repetition_stop_count += 1


def transcribe_window(
    *,
    asr: ASREngine,
    store: AudioStore,
    window: InferenceWindow,
    language_state: LanguageState,
    user_prompt: str,
    previous_tail: str,
    cancelled: CancelCallback,
) -> ASRResult:
    if cancelled():
        raise InterruptedError("Job cancelled")
    waveform = store.read(window.input_start_sample, window.input_end_sample)
    speech_seconds = window.speech_samples / float(store.sample_rate)
    input_seconds = window.input_sample_count / float(store.sample_rate)
    budget = ASR_WINDOW_MAX_NEW_TOKENS
    prompt = build_asr_prompt(user_prompt, previous_tail)
    attempted_language = language_state.inference_language
    try:
        raw = asr.transcribe(
            waveform,
            language=attempted_language,
            prompt=prompt,
            max_new_tokens=budget,
            is_cancelled=cancelled,
        )
        language, text, generated, reported_budget, hit_limit, repetition_detected = (
            _parse_asr_response(raw, requested_budget=budget)
        )
    except InterruptedError:
        raise
    except _ASRProtocolError:
        raise
    except Exception as exc:
        if str(exc).startswith("ASR_PROTOCOL_PARSE_FAILED"):
            raise _ASRProtocolError("ASR_PROTOCOL_PARSE_FAILED") from exc
        if _is_backend_runtime_error(exc):
            raise RuntimeError("ASR_BACKEND_RUNTIME_ERROR") from exc
        raise RuntimeError(f"ASR_MODEL_ERROR:{type(exc).__name__}") from exc

    abnormal = _is_abnormal_text(text)
    if abnormal:
        return ASRResult(
            False,
            language_state.trusted_or_requested,
            "",
            generated,
            reported_budget,
            1,
            "ABNORMAL_TRANSCRIPT",
        )
    if repetition_detected:
        logging.getLogger("davinci_asr.pipeline").warning(
            "ASR_REPETITION_DETECTED window=%d input_seconds=%.3f "
            "speech_seconds=%.3f generated_tokens=%d "
            "detected_language=%s transcript_character_count=%d",
            window.index,
            input_seconds,
            speech_seconds,
            generated,
            language or "Auto",
            len(text),
        )
        return ASRResult(
            False,
            language_state.trusted_or_requested,
            "",
            generated,
            reported_budget,
            1,
            "ASR_REPETITION_DETECTED",
        )
    if hit_limit:
        ended_normally = bool(raw.get("ended_normally", False))
        error = (
            "ASR_TOKEN_LIMIT_REACHED "
            f"window={window.index} input_seconds={input_seconds:.3f} "
            f"speech_seconds={speech_seconds:.3f} attempt=1 budget={budget} "
            f"generated_tokens={generated} "
            f"ended_normally={str(ended_normally).lower()} "
            f"abnormal={str(abnormal).lower()}"
        )
        logging.getLogger("davinci_asr.pipeline").warning(
            "%s detected_language=%s transcript_character_count=%d",
            error,
            language or "Auto",
            len(text),
        )
        return ASRResult(
            False,
            language_state.trusted_or_requested,
            "",
            generated,
            reported_budget,
            1,
            "ASR_TOKEN_LIMIT_REACHED",
        )
    if not text and window.speech_samples >= int(
        round(VAD_MIN_SPEECH_MS * store.sample_rate / 1000.0)
    ):
        return ASRResult(
            False,
            language_state.trusted_or_requested,
            "",
            generated,
            reported_budget,
            1,
            "EMPTY_TRANSCRIPT_FOR_SPEECH",
        )
    if text and language_state.automatic and not language_state.trusted_language:
        if not language:
            raise _ASRProtocolError("MALFORMED_LANGUAGE")
        language_state.accept(language, text)
    trusted = language_state.accept(language, text)
    return ASRResult(
        True,
        trusted,
        text,
        generated,
        reported_budget,
        1,
    )


def _align_window(
    *,
    aligner: AlignerEngine,
    store: AudioStore,
    window: InferenceWindow,
    transcript: str,
    language: str,
    cancelled: CancelCallback,
) -> AlignmentResult:
    waveform = store.read(window.input_start_sample, window.input_end_sample)
    input_start = window.input_start_sample / float(store.sample_rate)
    input_end = window.input_end_sample / float(store.sample_rate)
    result = aligner.align_with_quality(
        waveform,
        transcript,
        language,
        offset_seconds=input_start,
        input_start_seconds=input_start,
        input_end_seconds=input_end,
        is_cancelled=cancelled,
    )
    if not result.valid:
        return result
    restored = restore_punctuation(transcript, result.tokens)
    return AlignmentResult(restored, result.text_coverage, True)


def _recovery_window(
    *,
    index: int,
    region: SpeechRegion,
    speech_regions: list[SpeechRegion],
    total_samples: int,
    sample_rate: int,
) -> InferenceWindow:
    context = int(round(RECOVERY_CONTEXT_SECONDS * sample_rate))
    return InferenceWindow(
        index=index,
        core_start_sample=region.start_sample,
        core_end_sample=region.end_sample,
        input_start_sample=max(0, region.start_sample - context),
        input_end_sample=min(total_samples, region.end_sample + context),
        speech_samples=speech_samples_in_region(speech_regions, region),
        boundary_reason="coverage_recovery",
    )


class TranscriptionPipeline:
    def __init__(
        self,
        paths: RuntimePaths,
        hardware: HardwareProfile,
        *,
        model_manager: ModelManager | None = None,
        asr_factory: Callable[[Path, HardwareProfile], ASREngine] = QwenASREngine,
        aligner_factory: Callable[
            [Path, HardwareProfile], AlignerEngine
        ] = QwenForcedAlignerEngine,
        inference_service: InferenceService | None = None,
        speech_detector: SpeechDetector | None = None,
    ) -> None:
        self.paths = paths
        self.hardware = hardware
        self.models = model_manager or ModelManager(paths)
        self.inference = inference_service or InferenceService(
            self.models,
            hardware,
            asr_factory=asr_factory,
            aligner_factory=aligner_factory,
        )
        self._asr_factory = asr_factory
        self._aligner_factory = aligner_factory
        self.speech_detector = speech_detector or SileroVAD()
        self._owns_inference = inference_service is None
        self.log = logging.getLogger("davinci_asr.pipeline")

    def _build_subtitle_blocks(
        self,
        tokens: list[TimedToken],
        *,
        language: str,
        max_chars: int,
        remove_gaps: bool,
        trim_end_punctuation: bool,
    ) -> list[SubtitleBlock]:
        blocks = segment_tokens(
            tokens,
            language=language,
            max_chars=max_chars,
            remove_gaps=remove_gaps,
            trim_end_punctuation=trim_end_punctuation,
        )
        self.log.info(
            "provider_max_chars=%d effective_max_chars=%d subtitle_blocks=%d",
            max_chars,
            effective_max_chars(max_chars, language),
            len(blocks),
        )
        return blocks

    @staticmethod
    def _diagnostic_payload(
        *,
        sample_rate: int,
        speech_regions: list[SpeechRegion],
        records: list[WindowRecord],
        recovery_regions: list[SpeechRegion],
        remaining_coverage_holes: list[SpeechRegion],
    ) -> dict[str, object]:
        return {
            "speech_regions": [
                region.to_dict(sample_rate) for region in speech_regions
            ],
            "windows": [record.diagnostic(sample_rate) for record in records],
            "recovery_regions": [
                region.to_dict(sample_rate) for region in recovery_regions
            ],
            "remaining_coverage_holes": [
                region.to_dict(sample_rate) for region in remaining_coverage_holes
            ],
        }

    def _detect_script_language(
        self,
        *,
        request: JobRequest,
        store: AudioStore,
        metrics: PerformanceMetrics,
        stats: PipelineStats,
        update: StatusCallback,
        cancelled: CancelCallback,
    ) -> str:
        update("preparing", 7, "Detecting speech for language identification")
        vad_started = time.monotonic()
        speech_regions = self.speech_detector.detect(store, is_cancelled=cancelled)
        metrics.milliseconds("vad_ms", time.monotonic() - vad_started)
        windows = plan_windows(
            speech_regions=speech_regions,
            total_samples=store.sample_count,
            sample_rate=store.sample_rate,
        )
        metrics.values["script_language_detection_window_count"] = len(windows)
        if not windows:
            raise RuntimeError("No speech was detected")

        update("loading_asr", 10, "Loading Qwen3 ASR for language detection")
        asr, load_ms = self.inference.acquire_asr(request.asr_model)
        _accumulate_metric_ms(metrics, "asr_model_load_ms", load_ms)
        state = LanguageState("Auto")
        errors: list[str] = []
        try:
            for index, window in enumerate(windows):
                update(
                    "transcribing",
                    12 + int(8 * (index + 1) / len(windows)),
                    f"Detecting language {index + 1}/{len(windows)}",
                )
                inference_started = time.monotonic()
                try:
                    result = transcribe_window(
                        asr=asr,
                        store=store,
                        window=window,
                        language_state=state,
                        user_prompt="",
                        previous_tail="",
                        cancelled=cancelled,
                    )
                finally:
                    stats.asr_inference_seconds += time.monotonic() - inference_started
                _record_asr_result(stats, result)
                if result.valid and state.trusted_language:
                    metrics.values["script_language_detection_asr_calls"] = (
                        stats.asr_attempt_count
                    )
                    return state.trusted_language
                errors.append(result.error or "LANGUAGE_NOT_DETECTED")
        finally:
            self.inference.release_after_stage("asr")
        raise RuntimeError("SCRIPT_MATCH_LANGUAGE_NOT_DETECTED:" + ",".join(errors))

    def _script_match_fallback(
        self,
        *,
        request: JobRequest,
        language: str,
        lines: list[ScriptLine],
        store: AudioStore,
        metrics: PerformanceMetrics,
        stats: PipelineStats,
        update: StatusCallback,
        cancelled: CancelCallback,
    ) -> tuple[list[SubtitleBlock], list[TimedToken], list[float], dict[str, object]]:
        fallback_job_id = f"{request.job_id}_script_fallback"
        fallback_dir = self.paths.temp / fallback_job_id
        fallback_request = JobRequest(
            protocol=PROTOCOL_VERSION,
            job_id=fallback_job_id,
            action="transcribe",
            mode=AUTO_SUBTITLE_MODE,
            audio_path=request.audio_path,
            language=language,
            prompt="",
            reference_text="",
            ui_language=request.ui_language,
            asr_model=request.asr_model,
            subtitle=SubtitleOptions(max_chars=42),
            timeline=TimelineSpec(
                fps=request.timeline.fps,
                start_frame=request.timeline.start_frame,
            ),
        )
        fallback_pipeline = TranscriptionPipeline(
            self.paths,
            self.hardware,
            model_manager=self.models,
            asr_factory=self._asr_factory,
            aligner_factory=self._aligner_factory,
            inference_service=self.inference,
            speech_detector=self.speech_detector,
        )
        try:
            update("loading_asr", 50, "Building ASR anchors for Script Match")
            auto_result = fallback_pipeline.run(
                fallback_request,
                status=lambda state, progress, message: update(
                    state,
                    50 + int(max(0, min(100, progress)) * 0.25),
                    f"Script Match fallback: {message}",
                ),
                is_cancelled=cancelled,
            )
            auto_metrics = auto_result.metrics
            stats.asr_inference_seconds += (
                float(auto_metrics.get("asr_inference_ms", 0.0)) / 1000.0
            )
            stats.aligner_inference_seconds += (
                float(auto_metrics.get("aligner_inference_ms", 0.0)) / 1000.0
            )
            stats.asr_attempt_count += int(auto_metrics.get("asr_attempt_count", 0))
            for source_key in (
                "asr_model_load_ms",
                "recovery_asr_model_load_ms",
                "cpu_self_repair_asr_load_ms",
            ):
                _accumulate_metric_ms(
                    metrics, "asr_model_load_ms", auto_metrics.get(source_key, 0.0)
                )
            for source_key in (
                "aligner_model_load_ms",
                "recovery_aligner_model_load_ms",
            ):
                _accumulate_metric_ms(
                    metrics,
                    "aligner_model_load_ms",
                    auto_metrics.get(source_key, 0.0),
                )
            metrics.values["fallback_auto_subtitle_count"] = len(auto_result.blocks)
            metrics.values["fallback_auto_rtf"] = auto_metrics.get("rtf")
            metrics.values["fallback_auto_partial_result"] = bool(
                auto_metrics.get("partial_result", False)
            )

            plan = build_anchor_plan(
                lines,
                auto_result.blocks,
                language=language,
                duration=store.duration_seconds,
            )
            if (
                plan.unmapped_line_indices
                or len(plan.windows) != len(lines)
                or plan.mapping_coverage + 1e-9 < FALLBACK_MIN_GLOBAL_ANCHOR_COVERAGE
            ):
                raise RuntimeError(
                    "SCRIPT_MATCH_FALLBACK_ANCHORS_UNRELIABLE "
                    f"coverage={plan.mapping_coverage:.4f} "
                    f"unmapped={plan.unmapped_line_indices}"
                )

            update(
                "loading_aligner",
                77,
                "Loading Qwen3 Forced Aligner for Script Match fallback",
            )
            aligner, load_ms = self.inference.acquire_aligner()
            _accumulate_metric_ms(metrics, "aligner_model_load_ms", load_ms)
            blocks: list[SubtitleBlock] = []
            tokens: list[TimedToken] = []
            coverages: list[float] = []
            try:
                for index, window in enumerate(plan.windows):
                    if cancelled():
                        raise InterruptedError("Job cancelled")
                    update(
                        "aligning",
                        79 + int(11 * (index + 1) / len(plan.windows)),
                        f"Aligning script line {index + 1}/{len(plan.windows)}",
                    )
                    start_sample = max(0, int(round(window.start * store.sample_rate)))
                    end_sample = min(
                        store.sample_count,
                        int(round(window.end * store.sample_rate)),
                    )
                    waveform = store.read(start_sample, end_sample)
                    input_start = start_sample / float(store.sample_rate)
                    input_end = end_sample / float(store.sample_rate)
                    inference_started = time.monotonic()
                    try:
                        alignment = aligner.align_with_quality(
                            waveform,
                            window.line.text,
                            language,
                            offset_seconds=input_start,
                            input_start_seconds=input_start,
                            input_end_seconds=input_end,
                            is_cancelled=cancelled,
                        )
                    finally:
                        stats.aligner_inference_seconds += (
                            time.monotonic() - inference_started
                        )
                    local_line = ScriptLine(
                        index=0,
                        line_id=window.line.line_id,
                        source_line_number=window.line.source_line_number,
                        text=window.line.text,
                        normalized_text=window.line.normalized_text,
                    )
                    mapping = map_aligned_tokens_to_lines(
                        alignment.tokens,
                        [local_line],
                        language=language,
                        minimum_line_coverage=FALLBACK_MIN_MAPPING_COVERAGE,
                    )
                    if (
                        not alignment.valid
                        or alignment.text_coverage + 1e-9
                        < FALLBACK_MIN_ALIGNMENT_COVERAGE
                        or mapping.unmapped_line_indices
                        or len(mapping.blocks) != 1
                    ):
                        raise RuntimeError(
                            "SCRIPT_MATCH_FALLBACK_LINE_UNRELIABLE "
                            f"line_id={window.line.line_id} "
                            f"alignment_coverage={alignment.text_coverage:.4f} "
                            f"mapping_coverage={mapping.mapping_coverage:.4f} "
                            f"error={alignment.error}"
                        )
                    mapped = mapping.blocks[0]
                    blocks.append(
                        SubtitleBlock(mapped.start, mapped.end, window.line.text)
                    )
                    tokens.extend(alignment.tokens)
                    coverages.append(alignment.text_coverage)
            finally:
                self.inference.release_after_stage("forced_aligner")

            diagnostics = interval_diagnostics(blocks, store.duration_seconds)
            if any(
                (
                    diagnostics.invalid_interval_count,
                    diagnostics.non_monotonic_interval_count,
                    diagnostics.overlap_count,
                    diagnostics.out_of_bounds_count,
                )
            ):
                raise RuntimeError(
                    "SCRIPT_MATCH_FALLBACK_INTERVALS_INVALID "
                    f"invalid={diagnostics.invalid_interval_count} "
                    f"non_monotonic={diagnostics.non_monotonic_interval_count} "
                    f"overlap={diagnostics.overlap_count} "
                    f"out_of_bounds={diagnostics.out_of_bounds_count}"
                )
            return (
                blocks,
                tokens,
                coverages,
                {
                    "fallback_anchor_mapping_coverage": round(plan.mapping_coverage, 6),
                    "fallback_anchor_line_coverages": [
                        round(value, 6) for value in plan.line_coverages
                    ],
                },
            )
        finally:
            if fallback_dir.is_dir() and fallback_dir.parent == self.paths.temp:
                shutil.rmtree(fallback_dir, ignore_errors=True)

    @staticmethod
    def _quantize_script_blocks(
        blocks: list[SubtitleBlock],
        *,
        fps: str,
        start_frame: int,
        duration: float,
    ) -> list[SubtitleBlock]:
        output = quantize_blocks(
            blocks,
            fps,
            start_frame=start_frame,
            remove_gaps=False,
        )
        for block in output:
            if block.end > duration:
                block.end = duration
            if block.end <= block.start:
                raise RuntimeError("SCRIPT_MATCH_FRAME_QUANTIZATION_FAILED")
        diagnostics = interval_diagnostics(output, duration)
        if any(
            (
                diagnostics.invalid_interval_count,
                diagnostics.non_monotonic_interval_count,
                diagnostics.overlap_count,
                diagnostics.out_of_bounds_count,
            )
        ):
            raise RuntimeError("SCRIPT_MATCH_QUANTIZED_INTERVALS_INVALID")
        return output

    def _run_script_match(
        self,
        *,
        request: JobRequest,
        store: AudioStore,
        cache_dir: Path,
        started: float,
        metrics: PerformanceMetrics,
        stats: PipelineStats,
        update: StatusCallback,
        cancelled: CancelCallback,
    ) -> JobResult:
        lines = parse_reference_lines(request.reference_text)
        reference_text = reference_alignment_text(lines)
        duration = store.duration_seconds
        metrics.values.update(
            transcription_mode=SCRIPT_MATCH_MODE,
            script_reference_line_count=len(lines),
            script_reference_character_count=sum(
                len(line.normalized_text) for line in lines
            ),
            script_max_chars_ignored=True,
            script_remove_gaps_ignored=False,
            script_trim_end_punctuation_ignored=False,
            script_remove_gaps_applied=request.subtitle.remove_gaps,
            script_trim_end_punctuation_applied=(
                request.subtitle.trim_end_punctuation
            ),
            asr_model_load_ms=0.0,
            aligner_model_load_ms=0.0,
        )

        language = request.language
        if language == "Auto":
            language = self._detect_script_language(
                request=request,
                store=store,
                metrics=metrics,
                stats=stats,
                update=update,
                cancelled=cancelled,
            )

        direct_attempted = duration <= DIRECT_MAX_AUDIO_SECONDS
        direct_alignment = AlignmentResult(
            [], 0.0, False, "AUDIO_TOO_LONG_FOR_DIRECT_ALIGNMENT"
        )
        direct_mapping = ScriptMapping(
            blocks=[],
            mapping_coverage=0.0,
            line_coverages=[0.0 for _line in lines],
            unmapped_line_indices=[line.index for line in lines],
            reference_character_count=sum(len(line.normalized_text) for line in lines),
            matched_character_count=0,
        )
        direct_reasons: list[str] = []
        direct_unique_ratio = 0.0
        direct_span_ratio = 0.0
        direct_locally_collapsed_lines: tuple[int, ...] = ()
        direct_tokens: list[TimedToken] = []
        if direct_attempted:
            update("loading_aligner", 22, "Loading Qwen3 Forced Aligner")
            aligner, load_ms = self.inference.acquire_aligner()
            _accumulate_metric_ms(metrics, "aligner_model_load_ms", load_ms)
            try:
                update("aligning", 30, "Directly aligning the complete script")
                waveform = store.read(0, store.sample_count)
                inference_started = time.monotonic()
                try:
                    direct_alignment = aligner.align_with_quality(
                        waveform,
                        reference_text,
                        language,
                        offset_seconds=0.0,
                        input_start_seconds=0.0,
                        input_end_seconds=duration,
                        is_cancelled=cancelled,
                    )
                finally:
                    stats.aligner_inference_seconds += (
                        time.monotonic() - inference_started
                    )
                direct_tokens = list(direct_alignment.tokens)
                direct_mapping = map_aligned_tokens_to_lines(
                    direct_tokens,
                    lines,
                    language=language,
                    minimum_line_coverage=DIRECT_MIN_LINE_COVERAGE,
                )
                quality = evaluate_direct_quality(
                    direct_alignment,
                    direct_mapping,
                    line_count=len(lines),
                    duration=duration,
                )
                direct_reasons.extend(quality.reasons)
                direct_unique_ratio = quality.unique_interval_ratio
                direct_span_ratio = quality.aligned_span_ratio
                direct_locally_collapsed_lines = (
                    quality.locally_collapsed_line_indices
                )
            except InterruptedError:
                raise
            except Exception as exc:
                direct_reasons.append(f"DIRECT_EXCEPTION:{type(exc).__name__}")
                self.log.warning(
                    "job=%s script_match_direct_failed error_type=%s",
                    request.job_id,
                    type(exc).__name__,
                )
                self.inference.release_models()
            finally:
                self.inference.release_after_stage("forced_aligner")
        else:
            direct_reasons.append("AUDIO_TOO_LONG_FOR_DIRECT_ALIGNMENT")

        direct_success = direct_attempted and not direct_reasons
        fallback_used = not direct_success
        fallback_metrics: dict[str, object] = {}
        if direct_success:
            blocks = direct_mapping.blocks
            tokens = direct_tokens
            alignment_coverages = [direct_alignment.text_coverage]
            path = "direct"
        else:
            update("preparing", 48, "Direct alignment needs ASR-anchor fallback")
            blocks, tokens, alignment_coverages, fallback_metrics = (
                self._script_match_fallback(
                    request=request,
                    language=language,
                    lines=lines,
                    store=store,
                    metrics=metrics,
                    stats=stats,
                    update=update,
                    cancelled=cancelled,
                )
            )
            path = "asr_anchor_local_alignment"

        update("segmenting", 91, "Preserving script line boundaries")
        subtitle_started = time.monotonic()
        blocks = self._quantize_script_blocks(
            blocks,
            fps=request.timeline.fps,
            start_frame=request.timeline.start_frame,
            duration=duration,
        )
        preservation_count = sum(
            block.text == line.text for block, line in zip(blocks, lines)
        )
        if len(blocks) != len(lines) or preservation_count != len(lines):
            raise RuntimeError("SCRIPT_MATCH_LINE_PRESERVATION_FAILED")
        pre_postprocess_block_count = len(blocks)
        blocks = postprocess_subtitle_blocks(
            blocks,
            remove_gaps=request.subtitle.remove_gaps,
            trim_end_punctuation=request.subtitle.trim_end_punctuation,
        )
        diagnostics = interval_diagnostics(blocks, duration)
        metrics.milliseconds("subtitle_build_ms", time.monotonic() - subtitle_started)
        metrics.milliseconds("asr_inference_ms", stats.asr_inference_seconds)
        metrics.milliseconds("aligner_inference_ms", stats.aligner_inference_seconds)
        metrics.values.update(
            script_line_mapping_unit=(
                "characters" if language in {"Chinese", "Cantonese"} else "words"
            ),
            script_match_path=path,
            script_direct_alignment_attempted=direct_attempted,
            script_direct_alignment_success=direct_success,
            script_direct_alignment_coverage=round(direct_alignment.text_coverage, 6),
            script_direct_mapping_coverage=round(direct_mapping.mapping_coverage, 6),
            script_direct_line_coverages=[
                round(value, 6) for value in direct_mapping.line_coverages
            ],
            script_direct_unique_interval_ratio=round(direct_unique_ratio, 6),
            script_direct_aligned_span_ratio=round(direct_span_ratio, 6),
            script_direct_locally_collapsed_line_indices=[
                index + 1 for index in direct_locally_collapsed_lines
            ],
            script_fallback_used=fallback_used,
            script_fallback_trigger="|".join(dict.fromkeys(direct_reasons)),
            script_output_line_count=len(blocks),
            script_postprocess_removed_block_count=(
                pre_postprocess_block_count - len(blocks)
            ),
            script_unmapped_line_count=0,
            script_line_preservation_rate=round(preservation_count / len(lines), 6),
            alignment_text_coverage=[round(value, 6) for value in alignment_coverages],
            alignment_invalid_count=0,
            invalid_interval_count=diagnostics.invalid_interval_count,
            non_monotonic_interval_count=(diagnostics.non_monotonic_interval_count),
            overlap_count=diagnostics.overlap_count,
            out_of_bounds_count=diagnostics.out_of_bounds_count,
            remaining_coverage_hole_count=0,
            remaining_coverage_hole_seconds=0.0,
            partial_result=False,
            asr_attempt_count=stats.asr_attempt_count,
            asr_skipped=stats.asr_attempt_count == 0,
            direct_script_match_skipped_asr=(
                direct_success
                and request.language in {"Chinese", "English"}
                and stats.asr_attempt_count == 0
            ),
            **fallback_metrics,
        )

        output = cache_dir / f"{request.job_id}.srt"
        update("writing_srt", 95, "Writing UTF-8 SRT")
        write_srt(output, blocks, preserve_text=True)
        elapsed = time.monotonic() - started
        metric_values = metrics.finish(
            audio_duration=duration,
            total_seconds=elapsed,
        )
        self.log.info(
            "job=%s performance=%s",
            request.job_id,
            json.dumps(metric_values, sort_keys=True, separators=(",", ":")),
        )
        return JobResult(
            protocol=PROTOCOL_VERSION,
            job_id=request.job_id,
            language=language,
            transcript=reference_text,
            alignment_count=len(tokens),
            srt_path=str(output),
            blocks=blocks,
            backend=self.hardware.backend,
            elapsed_seconds=elapsed,
            metrics=metric_values,
        )

    def run(
        self,
        request: JobRequest,
        *,
        status: StatusCallback | None = None,
        is_cancelled: CancelCallback | None = None,
    ) -> JobResult:
        update = status or (lambda _state, _progress, _message: None)
        cancelled = is_cancelled or (lambda: False)
        started = time.monotonic()
        metrics = PerformanceMetrics()
        stats = PipelineStats()
        cache_dir = self.paths.temp / request.job_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        audio_store: AudioStore | None = None
        update("preparing", 3, "Normalizing timeline audio")
        try:
            audio_started = time.monotonic()
            audio_store = prepare_audio_store(
                request.audio_path,
                cache_dir / "audio_16k.wav",
                is_cancelled=cancelled,
            )
            metrics.milliseconds("audio_prepare_ms", time.monotonic() - audio_started)
            duration = audio_store.duration_seconds
            metrics.values["backend"] = self.hardware.backend
            if cancelled():
                raise InterruptedError("Job cancelled")

            if request.mode == SCRIPT_MATCH_MODE:
                return self._run_script_match(
                    request=request,
                    store=audio_store,
                    cache_dir=cache_dir,
                    started=started,
                    metrics=metrics,
                    stats=stats,
                    update=update,
                    cancelled=cancelled,
                )

            update("preparing", 7, "Detecting speech")
            vad_started = time.monotonic()
            speech_regions = self.speech_detector.detect(
                audio_store, is_cancelled=cancelled
            )
            metrics.milliseconds("vad_ms", time.monotonic() - vad_started)
            windows = plan_windows(
                speech_regions=speech_regions,
                total_samples=audio_store.sample_count,
                sample_rate=audio_store.sample_rate,
            )
            metrics.values.update(
                vad_speech_region_count=len(speech_regions),
                vad_speech_seconds=round(
                    sum(region.sample_count for region in speech_regions)
                    / float(audio_store.sample_rate),
                    3,
                ),
                window_count=len(windows),
                asr_window_max_new_tokens=ASR_WINDOW_MAX_NEW_TOKENS,
                window_core_seconds=[
                    round(window.sample_count / float(audio_store.sample_rate), 3)
                    for window in windows
                ],
                window_input_seconds=[
                    round(
                        window.input_sample_count / float(audio_store.sample_rate),
                        3,
                    )
                    for window in windows
                ],
                window_boundary_reason=[window.boundary_reason for window in windows],
                window_target_seconds=WINDOW_TARGET_SECONDS,
                window_soft_max_seconds=WINDOW_SOFT_MAX_SECONDS,
                window_hard_max_seconds=WINDOW_HARD_MAX_SECONDS,
                window_context_seconds=WINDOW_CONTEXT_SECONDS,
                window_min_core_seconds=WINDOW_MIN_CORE_SECONDS,
            )

            language_state = LanguageState(request.language)
            records: list[WindowRecord] = []
            previous_tail = ""

            update("loading_asr", 10, "Loading Qwen3 ASR")
            asr, asr_load_ms = self.inference.acquire_asr(request.asr_model)
            metrics.values["asr_model_load_ms"] = round(asr_load_ms, 3)
            try:
                for index, window in enumerate(windows):
                    update(
                        "transcribing",
                        15 + int(30 * (index + 1) / max(1, len(windows))),
                        f"Transcribing window {index + 1}/{len(windows)}",
                    )
                    inference_started = time.monotonic()
                    try:
                        asr_result = transcribe_window(
                            asr=asr,
                            store=audio_store,
                            window=window,
                            language_state=language_state,
                            user_prompt=request.prompt,
                            previous_tail=previous_tail,
                            cancelled=cancelled,
                        )
                    finally:
                        stats.asr_inference_seconds += (
                            time.monotonic() - inference_started
                        )
                    _record_asr_result(stats, asr_result)
                    record = WindowRecord(window, asr_result)
                    records.append(record)
                    previous_tail = (
                        asr_result.text[-PREVIOUS_TAIL_CHARACTERS:]
                        if asr_result.valid and asr_result.text
                        else ""
                    )
            finally:
                update("unloading_asr", 48, "Releasing Qwen3 ASR")
                self.inference.release_after_stage("asr")
            metrics.milliseconds("asr_inference_ms", stats.asr_inference_seconds)

            detected_language = language_state.trusted_or_requested
            if not detected_language:
                if speech_regions:
                    raise RuntimeError("ASR_LANGUAGE_NOT_DETECTED")
                raise RuntimeError("No speech was detected")

            update("loading_aligner", 55, "Loading Qwen3 Forced Aligner")
            aligner, aligner_load_ms = self.inference.acquire_aligner()
            metrics.values["aligner_model_load_ms"] = round(aligner_load_ms, 3)
            initial_candidates: list[TokenCandidate] = []
            try:
                alignable = [
                    record for record in records if record.asr.valid and record.asr.text
                ]
                for index, record in enumerate(alignable):
                    update(
                        "aligning",
                        58 + int(20 * (index + 1) / max(1, len(alignable))),
                        f"Aligning window {index + 1}/{len(alignable)}",
                    )
                    inference_started = time.monotonic()
                    try:
                        alignment = _align_window(
                            aligner=aligner,
                            store=audio_store,
                            window=record.window,
                            transcript=record.asr.text,
                            language=detected_language,
                            cancelled=cancelled,
                        )
                    finally:
                        stats.aligner_inference_seconds += (
                            time.monotonic() - inference_started
                        )
                    record.alignment = alignment
                    if not alignment.valid:
                        stats.alignment_invalid_count += 1
                        continue
                    core_candidates = build_token_candidates(
                        record.window,
                        alignment,
                        sample_rate=audio_store.sample_rate,
                    )
                    if not core_candidates:
                        stats.alignment_invalid_count += 1
                        record.alignment = AlignmentResult(
                            alignment.tokens,
                            alignment.text_coverage,
                            False,
                            "EMPTY_CORE_ALIGNMENT",
                        )
                        continue
                    record.core_claimed = True
                    initial_candidates.extend(core_candidates)
            finally:
                self.inference.release_after_stage("forced_aligner")

            recovery_core_max_samples = max(
                1,
                int(round(RECOVERY_CORE_MAX_SECONDS * audio_store.sample_rate)),
            )
            auditable_speech_regions = split_speech_regions(
                speech_regions,
                boundaries=[window.core_end_sample for window in windows[:-1]],
                max_samples=recovery_core_max_samples,
            )
            initial_holes = find_unclaimed_speech_regions(
                auditable_speech_regions,
                initial_candidates,
                audio_store.sample_rate,
            )
            initial_holes = merge_regions(
                initial_holes,
                total_samples=audio_store.sample_count,
                sample_rate=audio_store.sample_rate,
            )
            recovery_regions = split_recovery_regions(
                initial_holes,
                max_samples=recovery_core_max_samples,
            )
            metrics.values["initial_coverage_hole_count"] = len(initial_holes)
            metrics.values["initial_coverage_hole_seconds"] = round(
                sum(
                    speech_samples_in_region(speech_regions, region)
                    for region in initial_holes
                )
                / float(audio_store.sample_rate),
                3,
            )
            metrics.values["recovery_region_count"] = len(recovery_regions)
            metrics.values["recovery_context_seconds"] = RECOVERY_CONTEXT_SECONDS
            metrics.values["recovery_core_max_seconds"] = RECOVERY_CORE_MAX_SECONDS

            recovered_candidates: list[TokenCandidate] = []
            recovery_records: list[WindowRecord] = []
            if recovery_regions:
                update("loading_asr", 78, "Loading Qwen3 ASR for coverage recovery")
                asr, recovery_asr_load_ms = self.inference.acquire_asr(
                    request.asr_model
                )
                metrics.values["recovery_asr_model_load_ms"] = round(
                    recovery_asr_load_ms, 3
                )
                try:
                    for index, region in enumerate(recovery_regions):
                        window = _recovery_window(
                            index=len(windows) + index,
                            region=region,
                            speech_regions=speech_regions,
                            total_samples=audio_store.sample_count,
                            sample_rate=audio_store.sample_rate,
                        )
                        update(
                            "transcribing",
                            79 + int(3 * (index + 1) / len(recovery_regions)),
                            f"Recovering speech region {index + 1}/{len(recovery_regions)}",
                        )
                        inference_started = time.monotonic()
                        try:
                            asr_result = transcribe_window(
                                asr=asr,
                                store=audio_store,
                                window=window,
                                language_state=language_state,
                                user_prompt=request.prompt,
                                previous_tail="",
                                cancelled=cancelled,
                            )
                        finally:
                            stats.asr_inference_seconds += (
                                time.monotonic() - inference_started
                            )
                        _record_asr_result(stats, asr_result)
                        recovery_records.append(
                            WindowRecord(window, asr_result, recovery=True)
                        )
                finally:
                    self.inference.release_after_stage("asr")

                cpu_self_repair_records = [
                    record
                    for record in recovery_records
                    if not record.asr.valid
                    and record.asr.error in CPU_SELF_REPAIR_ERRORS
                ]
                if self.hardware.device == "mps" and cpu_self_repair_records:
                    update(
                        "loading_asr",
                        82,
                        "Loading CPU ASR for bounded self-repair",
                    )
                    self.inference.release_models()
                    cpu_profile = HardwareProfile(
                        platform=self.hardware.platform,
                        architecture=self.hardware.architecture,
                        device="cpu",
                        backend="CPU",
                        dtype="float32",
                        name=self.hardware.name,
                    )
                    cpu_asr = self._asr_factory(
                        self.models.model_path(request.asr_model),
                        cpu_profile,
                    )
                    cpu_load_started = time.monotonic()
                    cpu_asr.load()
                    metrics.values["cpu_self_repair_asr_load_ms"] = round(
                        (time.monotonic() - cpu_load_started) * 1000.0,
                        3,
                    )
                    try:
                        for index, record in enumerate(cpu_self_repair_records):
                            update(
                                "transcribing",
                                82,
                                "CPU self-repair "
                                f"{index + 1}/{len(cpu_self_repair_records)}",
                            )
                            previous_error = record.asr.error
                            inference_started = time.monotonic()
                            try:
                                asr_result = transcribe_window(
                                    asr=cpu_asr,
                                    store=audio_store,
                                    window=record.window,
                                    language_state=language_state,
                                    user_prompt=request.prompt,
                                    previous_tail="",
                                    cancelled=cancelled,
                                )
                            finally:
                                stats.asr_inference_seconds += (
                                    time.monotonic() - inference_started
                                )
                            _record_asr_result(stats, asr_result)
                            stats.cpu_self_repair_attempt_count += 1
                            record.asr = asr_result
                            record.self_repair_backend = "CPU"
                            record.self_repair_from_error = previous_error
                            if asr_result.valid:
                                stats.cpu_self_repair_success_count += 1
                    finally:
                        cpu_asr.unload()

                update(
                    "loading_aligner",
                    83,
                    "Loading Qwen3 Forced Aligner for coverage recovery",
                )
                aligner, recovery_aligner_load_ms = self.inference.acquire_aligner()
                metrics.values["recovery_aligner_model_load_ms"] = round(
                    recovery_aligner_load_ms, 3
                )
                try:
                    alignable_recovery = [
                        record
                        for record in recovery_records
                        if record.asr.valid and record.asr.text
                    ]
                    for index, record in enumerate(alignable_recovery):
                        update(
                            "aligning",
                            83 + int(2 * (index + 1) / max(1, len(alignable_recovery))),
                            f"Aligning recovery {index + 1}/{len(alignable_recovery)}",
                        )
                        inference_started = time.monotonic()
                        try:
                            alignment = _align_window(
                                aligner=aligner,
                                store=audio_store,
                                window=record.window,
                                transcript=record.asr.text,
                                language=detected_language,
                                cancelled=cancelled,
                            )
                        finally:
                            stats.aligner_inference_seconds += (
                                time.monotonic() - inference_started
                            )
                        record.alignment = alignment
                        if not alignment.valid:
                            stats.alignment_invalid_count += 1
                            continue
                        core_candidates = build_token_candidates(
                            record.window,
                            alignment,
                            sample_rate=audio_store.sample_rate,
                        )
                        if not core_candidates:
                            stats.alignment_invalid_count += 1
                            record.alignment = AlignmentResult(
                                alignment.tokens,
                                alignment.text_coverage,
                                False,
                                "EMPTY_CORE_ALIGNMENT",
                            )
                            continue
                        record.core_claimed = True
                        stats.recovery_success_count += 1
                        recovered_candidates.extend(core_candidates)
                finally:
                    self.inference.release_after_stage("forced_aligner")

            tokens, duplicate_count = reconcile_candidates(
                initial_candidates + recovered_candidates
            )
            remaining_holes = merge_regions(
                [
                    speech
                    for record in recovery_records
                    if not record.core_claimed
                    for speech in intersect_speech_regions(
                        speech_regions,
                        record.window.core_region,
                    )
                ],
                total_samples=audio_store.sample_count,
                sample_rate=audio_store.sample_rate,
            )
            remaining_hole_details = [
                region.to_dict(audio_store.sample_rate) for region in remaining_holes
            ]
            remaining_hole_seconds = round(
                sum(
                    speech_samples_in_region(speech_regions, region)
                    for region in remaining_holes
                )
                / float(audio_store.sample_rate),
                3,
            )
            records.extend(recovery_records)
            alignment_coverages = [
                round(record.alignment.text_coverage, 4)
                for record in records
                if record.alignment is not None
            ]
            metrics.values.update(
                asr_attempt_count=stats.asr_attempt_count,
                asr_token_limit_count=stats.asr_token_limit_count,
                asr_repetition_stop_count=stats.asr_repetition_stop_count,
                alignment_invalid_count=stats.alignment_invalid_count,
                alignment_text_coverage=alignment_coverages,
                recovery_success_count=stats.recovery_success_count,
                cpu_self_repair_attempt_count=(stats.cpu_self_repair_attempt_count),
                cpu_self_repair_success_count=(stats.cpu_self_repair_success_count),
                remaining_coverage_hole_count=len(remaining_holes),
                remaining_coverage_hole_seconds=remaining_hole_seconds,
                remaining_coverage_holes=remaining_hole_details,
                partial_result=bool(remaining_holes),
                overlap_duplicate_token_count=duplicate_count,
            )
            metrics.milliseconds("asr_inference_ms", stats.asr_inference_seconds)
            metrics.milliseconds(
                "aligner_inference_ms", stats.aligner_inference_seconds
            )
            atomic_write_json_fast(
                cache_dir / "chunks.json",
                self._diagnostic_payload(
                    sample_rate=audio_store.sample_rate,
                    speech_regions=speech_regions,
                    records=records,
                    recovery_regions=recovery_regions,
                    remaining_coverage_holes=remaining_holes,
                ),
            )
            if remaining_holes:
                first = remaining_holes[0]
                self.log.warning(
                    "job=%s warning=UNRESOLVED_SPEECH_REGION count=%d "
                    "seconds=%.3f first=%.3f-%.3f partial_result=true",
                    request.job_id,
                    len(remaining_holes),
                    remaining_hole_seconds,
                    first.start_sample / audio_store.sample_rate,
                    first.end_sample / audio_store.sample_rate,
                )
            if cancelled():
                raise InterruptedError("Job cancelled")

            update("segmenting", 86, "Creating subtitle blocks")
            subtitle_started = time.monotonic()
            blocks = self._build_subtitle_blocks(
                tokens,
                language=detected_language,
                max_chars=request.subtitle.max_chars,
                remove_gaps=request.subtitle.remove_gaps,
                trim_end_punctuation=request.subtitle.trim_end_punctuation,
            )
            if not blocks:
                raise RuntimeError("No aligned subtitle blocks were produced")
            blocks = quantize_blocks(
                blocks,
                request.timeline.fps,
                start_frame=request.timeline.start_frame,
                remove_gaps=request.subtitle.remove_gaps,
            )
            metrics.milliseconds(
                "subtitle_build_ms", time.monotonic() - subtitle_started
            )
            output = cache_dir / f"{request.job_id}.srt"
            update("writing_srt", 95, "Writing UTF-8 SRT")
            write_srt(output, blocks)
            elapsed = time.monotonic() - started
            metric_values = metrics.finish(
                audio_duration=duration,
                total_seconds=elapsed,
            )
            self.log.info(
                "job=%s performance=%s",
                request.job_id,
                json.dumps(metric_values, sort_keys=True, separators=(",", ":")),
            )
            return JobResult(
                protocol=PROTOCOL_VERSION,
                job_id=request.job_id,
                language=detected_language,
                transcript=join_token_text(tokens, detected_language),
                alignment_count=len(tokens),
                srt_path=str(output),
                blocks=blocks,
                backend=self.hardware.backend,
                elapsed_seconds=elapsed,
                metrics=metric_values,
            )
        except BaseException:
            self.inference.release_models()
            raise
        finally:
            if audio_store is not None:
                audio_store.remove()
            if self._owns_inference:
                self.inference.close()
