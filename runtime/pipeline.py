from __future__ import annotations

import json
import logging
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
    ALIGNMENT_LANGUAGES,
    ASR_WINDOW_MAX_NEW_TOKENS,
    PROTOCOL_VERSION,
    RECOVERY_CONTEXT_SECONDS,
    RECOVERY_CORE_MAX_SECONDS,
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
    TimedToken,
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
from runtime.subtitles.segmenter import (
    effective_max_chars,
    join_token_text,
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
            value["alignment_text_coverage"] = round(
                self.alignment.text_coverage, 4
            )
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
        if characters and Counter(characters).most_common(1)[0][1] / len(characters) >= 0.90:
            return True
        words = value.casefold().split()
        if len(words) >= 16 and Counter(words).most_common(1)[0][1] / len(words) >= 0.80:
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
                region.to_dict(sample_rate)
                for region in remaining_coverage_holes
            ],
        }

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
            asr, asr_load_ms = self.inference.acquire_asr()
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
                alignable = [record for record in records if record.asr.valid and record.asr.text]
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
                int(
                    round(
                        RECOVERY_CORE_MAX_SECONDS * audio_store.sample_rate
                    )
                ),
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
            metrics.values["recovery_core_max_seconds"] = (
                RECOVERY_CORE_MAX_SECONDS
            )

            recovered_candidates: list[TokenCandidate] = []
            recovery_records: list[WindowRecord] = []
            if recovery_regions:
                update("loading_asr", 78, "Loading Qwen3 ASR for coverage recovery")
                asr, recovery_asr_load_ms = self.inference.acquire_asr()
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
                        self.models.model_path("asr"),
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
                            83
                            + int(
                                2
                                * (index + 1)
                                / max(1, len(alignable_recovery))
                            ),
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
                region.to_dict(audio_store.sample_rate)
                for region in remaining_holes
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
                cpu_self_repair_attempt_count=(
                    stats.cpu_self_repair_attempt_count
                ),
                cpu_self_repair_success_count=(
                    stats.cpu_self_repair_success_count
                ),
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
