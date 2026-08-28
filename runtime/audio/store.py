from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.constants import AUDIO_STREAM_BLOCK_SECONDS, SAMPLE_RATE

CancelCallback = Callable[[], bool]


@dataclass(slots=True)
class AudioStore:
    path: Path
    sample_rate: int
    sample_count: int
    owned: bool = True
    _stream: Any | None = field(default=None, init=False, repr=False)

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / float(self.sample_rate)

    def open(self) -> "AudioStore":
        if self._stream is None:
            import soundfile as sf

            self._stream = sf.SoundFile(self.path, mode="r")
        return self

    def read(self, start_sample: int, end_sample: int):
        import numpy as np

        start = max(0, min(int(start_sample), self.sample_count))
        end = max(start, min(int(end_sample), self.sample_count))
        stream = self.open()._stream
        stream.seek(start)
        value = stream.read(
            frames=end - start,
            dtype="float32",
            always_2d=False,
        )
        return np.asarray(value, dtype=np.float32)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def remove(self) -> None:
        self.close()
        if self.owned:
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> "AudioStore":
        return self.open()

    def __exit__(self, *_args: object) -> None:
        self.close()


def _mono(value):
    import numpy as np

    data = np.asarray(value, dtype=np.float32)
    if data.ndim == 1:
        return data
    if data.ndim != 2:
        raise ValueError(f"Unsupported audio shape: {data.shape}")
    if data.shape[1] == 1:
        return data[:, 0]
    return data.mean(axis=1, dtype=np.float32)


def _check_cancelled(is_cancelled: CancelCallback | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise InterruptedError("Job cancelled")


def _scan_peak(source: Any, block_frames: int, is_cancelled: CancelCallback | None) -> float:
    import numpy as np

    source.seek(0)
    peak = 0.0
    while True:
        _check_cancelled(is_cancelled)
        block = source.read(frames=block_frames, dtype="float32", always_2d=True)
        if len(block) == 0:
            break
        mono = _mono(block)
        if not np.all(np.isfinite(mono)):
            raise ValueError("Audio contains NaN or infinite samples")
        peak = max(peak, float(np.max(np.abs(mono))))
    if source.frames <= 0:
        raise ValueError("Audio is empty")
    return peak


def _resampled_blocks(
    source: Any,
    *,
    source_rate: int,
    target_rate: int,
    block_frames: int,
    scale: float,
    is_cancelled: CancelCallback | None,
) -> Iterator[Any]:
    import numpy as np

    total = int(source.frames)
    if source_rate == target_rate:
        source.seek(0)
        while True:
            _check_cancelled(is_cancelled)
            block = source.read(frames=block_frames, dtype="float32", always_2d=True)
            if len(block) == 0:
                return
            yield np.clip(_mono(block) * scale, -1.0, 1.0).astype(
                np.float32, copy=False
            )

    from scipy.signal import resample_poly

    divisor = math.gcd(source_rate, target_rate)
    up = target_rate // divisor
    down = source_rate // divisor
    # scipy.signal.resample_poly's default FIR has half_len=10*max(up, down).
    # Align every extended window to the decimation phase and retain more than
    # the complete input-side filter support. The retained core is therefore
    # identical to a single full-duration resample_poly call.
    half_len = 10 * max(up, down)
    overlap = math.ceil(half_len / up) + 4
    overlap = math.ceil(overlap / down) * down
    block_frames = max(down, (block_frames // down) * down)

    for core_start in range(0, total, block_frames):
        _check_cancelled(is_cancelled)
        core_end = min(total, core_start + block_frames)
        extended_start = max(0, core_start - overlap)
        extended_start = (extended_start // down) * down
        extended_end = min(total, core_end + overlap)
        if extended_end < total:
            extended_end = min(total, math.ceil(extended_end / down) * down)
        source.seek(extended_start)
        source_value = source.read(
            frames=extended_end - extended_start,
            dtype="float32",
            always_2d=True,
        )
        mono = np.clip(_mono(source_value) * scale, -1.0, 1.0)
        converted = resample_poly(mono, up, down).astype(np.float32, copy=False)
        global_start = math.ceil(core_start * up / down)
        global_end = math.ceil(core_end * up / down)
        local_origin = extended_start * up // down
        yield converted[global_start - local_origin : global_end - local_origin]


def prepare_audio_store(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    target_rate: int = SAMPLE_RATE,
    block_seconds: float = AUDIO_STREAM_BLOCK_SECONDS,
    is_cancelled: CancelCallback | None = None,
) -> AudioStore:
    """Return direct compatible WAV input or normalize into an owned audio store."""
    import numpy as np
    import soundfile as sf

    source_file = Path(source_path).expanduser().resolve()
    if not source_file.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {source_file.name}")
    _check_cancelled(is_cancelled)
    with sf.SoundFile(source_file, mode="r") as source:
        if (
            source.format in {"WAV", "WAVEX"}
            and str(source.subtype).startswith("PCM_")
            and int(source.samplerate) == int(target_rate)
            and int(source.channels) == 1
            and int(source.frames) > 0
        ):
            return AudioStore(
                source_file,
                int(source.samplerate),
                int(source.frames),
                owned=False,
            )
    destination = Path(destination_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    normalized_temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.normalized.tmp"
    )
    temporary.unlink(missing_ok=True)
    normalized_temporary.unlink(missing_ok=True)
    written = 0
    output_peak = 0.0
    try:
        with sf.SoundFile(source_file, mode="r") as source:
            source_rate = int(source.samplerate)
            if source_rate <= 0 or target_rate <= 0:
                raise ValueError("Sample rates must be positive")
            block_frames = max(1, int(round(source_rate * float(block_seconds))))
            peak = _scan_peak(source, block_frames, is_cancelled)
            scale = 1.0 / peak if peak > 1.0 else 1.0
            with sf.SoundFile(
                temporary,
                mode="w",
                samplerate=target_rate,
                channels=1,
                format="WAV",
                subtype="FLOAT",
            ) as output:
                for block in _resampled_blocks(
                    source,
                    source_rate=source_rate,
                    target_rate=target_rate,
                    block_frames=block_frames,
                    scale=scale,
                    is_cancelled=is_cancelled,
                ):
                    _check_cancelled(is_cancelled)
                    output.write(block)
                    written += len(block)
                    if len(block):
                        output_peak = max(output_peak, float(np.max(np.abs(block))))
                output.flush()
        if output_peak > 1.0:
            with (
                sf.SoundFile(temporary, mode="r") as source,
                sf.SoundFile(
                    normalized_temporary,
                    mode="w",
                    samplerate=target_rate,
                    channels=1,
                    format="WAV",
                    subtype="FLOAT",
                ) as output,
            ):
                while True:
                    _check_cancelled(is_cancelled)
                    block = source.read(
                        frames=max(1, int(round(target_rate * block_seconds))),
                        dtype="float32",
                        always_2d=False,
                    )
                    if len(block) == 0:
                        break
                    output.write(
                        np.clip(block / output_peak, -1.0, 1.0).astype(
                            np.float32,
                            copy=False,
                        )
                    )
                output.flush()
            os.replace(normalized_temporary, destination)
        else:
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        normalized_temporary.unlink(missing_ok=True)
    if written <= 0:
        destination.unlink(missing_ok=True)
        raise ValueError("Audio is empty")
    return AudioStore(destination, target_rate, written, owned=True)
