from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

from runtime.constants import SAMPLE_RATE


def to_mono(waveform: np.ndarray) -> np.ndarray:
    import numpy as np

    value = np.asarray(waveform)
    if value.ndim == 1:
        return value.astype(np.float32, copy=False)
    if value.ndim != 2:
        raise ValueError(f"Unsupported audio shape: {value.shape}")
    return value.astype(np.float32).mean(axis=1, dtype=np.float32)


def normalize_range(waveform: np.ndarray) -> np.ndarray:
    import numpy as np

    value = np.asarray(waveform, dtype=np.float32)
    if not np.all(np.isfinite(value)):
        raise ValueError("Audio contains NaN or infinite samples")
    if value.size == 0:
        raise ValueError("Audio is empty")
    peak = float(np.max(np.abs(value)))
    if peak > 1.0:
        value = value / peak
    return np.clip(value, -1.0, 1.0).astype(np.float32, copy=False)


def resample(waveform: np.ndarray, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    import numpy as np
    from scipy.signal import resample_poly

    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive")
    if source_rate == target_rate:
        return np.asarray(waveform, dtype=np.float32)
    divisor = int(np.gcd(source_rate, target_rate))
    return resample_poly(
        np.asarray(waveform, dtype=np.float32),
        target_rate // divisor,
        source_rate // divisor,
    ).astype(np.float32)


def load_and_normalize(path: str | Path, target_rate: int = SAMPLE_RATE) -> tuple[np.ndarray, int]:
    import soundfile as sf

    audio_path = Path(path).expanduser().resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path.name}")
    waveform, source_rate = sf.read(audio_path, dtype="float32", always_2d=False)
    mono = to_mono(waveform)
    normalized = normalize_range(mono)
    converted = resample(normalized, int(source_rate), target_rate)
    return normalize_range(converted), target_rate
