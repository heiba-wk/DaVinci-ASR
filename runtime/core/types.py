from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runtime.constants import (
    AUTO_SUBTITLE_MODE,
    ASR_MODEL_KEY,
    ASR_MODEL_KEYS,
    INTERFACE_LANGUAGES,
    JOB_STATES,
    PROTOCOL_VERSION,
    SCRIPT_MATCH_MODE,
    TRANSCRIPTION_MODES,
    UI_LANGUAGES,
)


@dataclass(slots=True)
class SubtitleOptions:
    max_chars: int = 42
    remove_gaps: bool = False
    trim_end_punctuation: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SubtitleOptions":
        raw = value or {}
        max_chars = int(raw.get("max_chars", 42))
        if not 1 <= max_chars <= 500:
            raise ValueError("subtitle.max_chars must be between 1 and 500")
        return cls(
            max_chars=max_chars,
            remove_gaps=bool(raw.get("remove_gaps", False)),
            trim_end_punctuation=bool(raw.get("trim_end_punctuation", False)),
        )


@dataclass(slots=True)
class TimelineSpec:
    fps: str = "24"
    start_frame: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "TimelineSpec":
        raw = value or {}
        return cls(
            fps=str(raw.get("fps", "24")), start_frame=int(raw.get("start_frame", 0))
        )


@dataclass(slots=True)
class JobRequest:
    protocol: int
    job_id: str
    action: str
    mode: str = AUTO_SUBTITLE_MODE
    audio_path: str = ""
    language: str = "Auto"
    prompt: str = ""
    reference_text: str = ""
    ui_language: str = "cn"
    asr_model: str = ASR_MODEL_KEY
    download_source: str = ""
    subtitle: SubtitleOptions = field(default_factory=SubtitleOptions)
    timeline: TimelineSpec = field(default_factory=TimelineSpec)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JobRequest":
        protocol = int(value.get("protocol", 0))
        if protocol != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported protocol: {protocol}")
        job_id = str(value.get("job_id", "")).strip()
        if not job_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for ch in job_id
        ):
            raise ValueError("job_id must contain only letters, digits, '-' and '_'")
        language = str(value.get("language", "Auto"))
        if language not in UI_LANGUAGES:
            raise ValueError(f"Unsupported language: {language}")
        ui_language = str(value.get("ui_language", "cn"))
        if ui_language not in INTERFACE_LANGUAGES:
            raise ValueError(f"Unsupported UI language: {ui_language}")
        asr_model = str(value.get("asr_model", ASR_MODEL_KEY)).strip()
        if asr_model not in ASR_MODEL_KEYS:
            raise ValueError(f"Unsupported ASR model: {asr_model}")
        action = str(value.get("action", "transcribe"))
        if action not in {
            "transcribe",
            "download_models",
            "self_test",
            "cleanup_temp",
            "shutdown",
        }:
            raise ValueError(f"Unsupported action: {action}")
        audio_path = str(value.get("audio_path", ""))
        if action == "transcribe" and not audio_path:
            raise ValueError("audio_path is required for transcribe")
        mode = str(value.get("mode", AUTO_SUBTITLE_MODE)).strip()
        if mode not in TRANSCRIPTION_MODES:
            raise ValueError(f"Unsupported transcription mode: {mode}")
        raw_reference = value.get("reference_text", "")
        reference_text = "" if raw_reference is None else str(raw_reference)
        if (
            action == "transcribe"
            and mode == SCRIPT_MATCH_MODE
            and not any(line.strip() for line in reference_text.splitlines())
        ):
            raise ValueError("reference_text requires at least one non-empty line")
        download_source = str(value.get("download_source", "")).strip()
        if action == "download_models" and download_source not in {
            "huggingface",
            "modelscope",
        }:
            raise ValueError("download_source must be 'huggingface' or 'modelscope'")
        return cls(
            protocol=protocol,
            job_id=job_id,
            action=action,
            mode=mode,
            audio_path=audio_path,
            language=language,
            prompt=str(value.get("prompt", "")),
            reference_text=reference_text,
            ui_language=ui_language,
            asr_model=asr_model,
            download_source=download_source,
            subtitle=SubtitleOptions.from_dict(value.get("subtitle")),
            timeline=TimelineSpec.from_dict(value.get("timeline")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class JobStatus:
    protocol: int
    job_id: str
    state: str
    progress: int
    message: str = ""
    error: str = ""
    download_status: str = ""
    download_source: str = ""
    download_next_source: str = ""
    download_current_file: str = ""
    download_model_name: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    download_speed_bps: int = 0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in JOB_STATES:
            raise ValueError(f"Invalid job state: {self.state}")
        self.progress = max(0, min(100, int(self.progress)))
        self.downloaded_bytes = max(0, int(self.downloaded_bytes))
        self.total_bytes = max(0, int(self.total_bytes))
        self.download_speed_bps = max(0, int(self.download_speed_bps))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TimedToken:
    text: str
    start: float
    end: float

    def __post_init__(self) -> None:
        self.start = float(self.start)
        self.end = float(self.end)
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"Invalid token interval: {self.start} -> {self.end}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SubtitleBlock:
    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        self.start = float(self.start)
        self.end = float(self.end)
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"Invalid subtitle interval: {self.start} -> {self.end}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HardwareProfile:
    platform: str
    architecture: str
    device: str
    backend: str
    dtype: str
    name: str = ""
    mps_failed: bool = False
    validated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class JobResult:
    protocol: int
    job_id: str
    language: str
    srt_path: str
    blocks: list[SubtitleBlock]
    backend: str
    elapsed_seconds: float
    transcript: str = ""
    alignment_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_transcript: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "protocol": self.protocol,
            "job_id": self.job_id,
            "language": self.language,
            "srt_path": str(Path(self.srt_path)),
            "blocks": [item.to_dict() for item in self.blocks],
            "backend": self.backend,
            "elapsed_seconds": round(float(self.elapsed_seconds), 3),
            "alignment_count": int(self.alignment_count),
            "metrics": dict(self.metrics),
        }
        if include_transcript:
            value["transcript"] = self.transcript
        return value
