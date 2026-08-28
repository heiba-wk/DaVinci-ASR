from __future__ import annotations

import gc
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

from runtime.core.hardware import release_accelerator_cache, torch_dtype
from runtime.core.types import HardwareProfile
from runtime.constants import ASR_WINDOW_MAX_NEW_TOKENS

ASR_REPETITION_THRESHOLD = 20
ASR_REPETITION_MAX_PATTERN_TOKENS = 20
ASR_REPETITION_CHECK_INTERVAL = 32


def has_repeated_token_pattern(
    token_ids: list[int],
    *,
    repetitions: int = ASR_REPETITION_THRESHOLD,
    max_pattern_tokens: int = ASR_REPETITION_MAX_PATTERN_TOKENS,
) -> bool:
    if repetitions < 2 or max_pattern_tokens < 1:
        raise ValueError("Invalid repetition detection settings")
    values = [int(value) for value in token_ids]
    largest_pattern = min(max_pattern_tokens, len(values) // repetitions)
    for pattern_size in range(1, largest_pattern + 1):
        pattern = values[-pattern_size:]
        if values[-pattern_size * repetitions :] == pattern * repetitions:
            return True
    return False


def generation_hit_token_limit(
    generated_tokens: int,
    max_new_tokens: int,
    *,
    ended_normally: bool,
) -> bool:
    if int(generated_tokens) < int(max_new_tokens):
        return False
    if ended_normally:
        return False
    return True


def _collect_token_ids(value: Any, output: set[int]) -> None:
    if value is None:
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect_token_ids(item, output)
        return
    try:
        output.add(int(value))
    except (TypeError, ValueError):
        return


class QwenASREngine:
    def __init__(
        self,
        model_path: str | Path,
        hardware: HardwareProfile,
        max_new_tokens: int | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.hardware = hardware
        if max_new_tokens is not None and int(max_new_tokens) <= 0:
            raise ValueError("max_new_tokens must be positive")
        self.max_new_tokens = int(max_new_tokens) if max_new_tokens is not None else None
        self.processor: Any | None = None
        self.model: Any | None = None

    def load(self) -> None:
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        if self.model is not None:
            return
        dtype = torch_dtype(self.hardware)
        if self.processor is None:
            self.processor = AutoProcessor.from_pretrained(
                self.model_path, local_files_only=True
            )
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.model_path,
            dtype=dtype,
            local_files_only=True,
        ).to(self.hardware.device)
        self.model.eval()
        if not isinstance(dtype, torch.dtype):
            raise RuntimeError("Invalid torch dtype")

    def transcribe(
        self,
        waveform: np.ndarray,
        *,
        language: str | None,
        prompt: str = "",
        max_new_tokens: int | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, str | int | bool]:
        import numpy as np
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        if self.model is None or self.processor is None:
            raise RuntimeError("ASR model is not loaded")
        audio = np.asarray(waveform, dtype=np.float32)
        requested_budget = max_new_tokens if max_new_tokens is not None else self.max_new_tokens
        budget = (
            int(requested_budget)
            if requested_budget is not None
            else ASR_WINDOW_MAX_NEW_TOKENS
        )
        if budget <= 0:
            raise ValueError("max_new_tokens must be positive")
        inputs = self.processor.apply_transcription_request(
            audio=audio,
            language=language,
            prompt=prompt or None,
        ).to(self.model.device, self.model.dtype)
        prompt_token_count = int(inputs["input_ids"].shape[-1])
        criteria: list[Any] = []
        if is_cancelled is not None:
            callback = is_cancelled

            class CancelStoppingCriteria(StoppingCriteria):
                def __call__(self, *_args: Any, **_kwargs: Any) -> bool:
                    return bool(callback())

            criteria.append(CancelStoppingCriteria())

        class RepetitionStoppingCriteria(StoppingCriteria):
            def __init__(self) -> None:
                self.triggered = False

            def __call__(self, input_ids: Any, *_args: Any, **_kwargs: Any) -> bool:
                generated_count = int(input_ids.shape[-1]) - prompt_token_count
                if (
                    generated_count < ASR_REPETITION_THRESHOLD
                    or generated_count % ASR_REPETITION_CHECK_INTERVAL != 0
                ):
                    return False
                maximum_tail = (
                    ASR_REPETITION_THRESHOLD
                    * ASR_REPETITION_MAX_PATTERN_TOKENS
                )
                tail_start = max(
                    prompt_token_count,
                    int(input_ids.shape[-1]) - maximum_tail,
                )
                sequence = input_ids[0, tail_start:]
                if hasattr(sequence, "detach"):
                    sequence = sequence.detach()
                if hasattr(sequence, "cpu"):
                    sequence = sequence.cpu()
                values = sequence.tolist()
                self.triggered = has_repeated_token_pattern(values)
                return self.triggered

        repetition_stopper = RepetitionStoppingCriteria()
        criteria.append(repetition_stopper)
        stopping_criteria = StoppingCriteriaList(criteria)
        with torch.inference_mode():
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=budget,
                do_sample=False,
                stopping_criteria=stopping_criteria,
            )
        if is_cancelled is not None and is_cancelled():
            raise InterruptedError("Job cancelled")
        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        decoded = self.processor.decode(generated, return_format="parsed")
        if not isinstance(decoded, (list, tuple)) or not decoded or not isinstance(decoded[0], dict):
            raise RuntimeError("ASR_PROTOCOL_PARSE_FAILED: parsed output is invalid")
        parsed = decoded[0]
        detected = parsed.get("language") or language or ""
        text = str(parsed.get("transcription", "")).strip()
        generated_tokens = int(generated.shape[-1])
        eos_ids: set[int] = set()
        _collect_token_ids(getattr(getattr(self.model, "generation_config", None), "eos_token_id", None), eos_ids)
        _collect_token_ids(getattr(getattr(self.model, "config", None), "eos_token_id", None), eos_ids)
        tokenizer = getattr(self.processor, "tokenizer", None)
        _collect_token_ids(getattr(tokenizer, "eos_token_id", None), eos_ids)
        ended_normally = False
        if generated_tokens and eos_ids:
            try:
                ended_normally = int(generated[0, -1].item()) in eos_ids
            except (AttributeError, IndexError, TypeError, ValueError):
                ended_normally = False
        hit_token_limit = generation_hit_token_limit(
            generated_tokens,
            budget,
            ended_normally=ended_normally,
        )
        return {
            "language": str(detected),
            "text": text,
            "generated_tokens": generated_tokens,
            "max_new_tokens": budget,
            "hit_token_limit": hit_token_limit,
            "ended_normally": ended_normally,
            "repetition_detected": repetition_stopper.triggered,
            "protocol_ok": True,
        }

    def release_model(self) -> None:
        self.model = None
        gc.collect()
        release_accelerator_cache(self.hardware)

    def unload(self) -> None:
        self.release_model()
        self.processor = None
