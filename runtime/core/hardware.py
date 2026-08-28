from __future__ import annotations

import os
import platform
import json
import time
from pathlib import Path
from typing import Any

from runtime.core.types import HardwareProfile
from runtime.core.paths import RuntimePaths


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Private runtime is missing its bundled PyTorch") from exc
    return torch


def detect_hardware(*, force_cpu: bool = False, mps_failed: bool = False) -> HardwareProfile:
    torch = _torch()
    system = platform.system()
    architecture = platform.machine()
    if not force_cpu and system == "Windows" and torch.cuda.is_available():
        bf16 = bool(getattr(torch.cuda, "is_bf16_supported", lambda: False)())
        return HardwareProfile(
            platform=system,
            architecture=architecture,
            device="cuda",
            backend="NVIDIA CUDA",
            dtype="bfloat16" if bf16 else "float16",
            name=torch.cuda.get_device_name(0),
        )
    if not force_cpu and system == "Darwin" and not mps_failed:
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
            return HardwareProfile(
                platform=system,
                architecture=architecture,
                device="mps",
                backend="Apple MPS",
                dtype="float16",
                name="Apple Silicon",
            )
    return HardwareProfile(
        platform=system,
        architecture=architecture,
        device="cpu",
        backend="CPU",
        dtype="float32",
        name=platform.processor() or architecture,
        mps_failed=mps_failed,
    )


def torch_dtype(profile: HardwareProfile) -> Any:
    torch = _torch()
    return {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }[profile.dtype]


def release_accelerator_cache(profile: HardwareProfile) -> None:
    torch = _torch()
    if profile.device == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif profile.device == "mps":
        mps = getattr(torch, "mps", None)
        empty_cache = getattr(mps, "empty_cache", None)
        if callable(empty_cache):
            empty_cache()


def _hardware_state_path(paths: RuntimePaths) -> Path:
    return paths.settings / "hardware_state.json"


def mps_was_disabled(paths: RuntimePaths) -> bool:
    try:
        value = json.loads(_hardware_state_path(paths).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return bool(value.get("mps_failed", False))


def mark_mps_failed(paths: RuntimePaths, reason: str) -> None:
    paths.settings.mkdir(parents=True, exist_ok=True)
    destination = _hardware_state_path(paths)
    temporary = destination.with_name(destination.name + ".tmp")
    # Store only the exception class/message fragment, never media or user paths.
    safe_reason = str(reason).replace(str(Path.home()), "<home>")[:500]
    temporary.write_text(
        json.dumps({"mps_failed": True, "reason": safe_reason}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def mark_hardware_validated(paths: RuntimePaths, profile: HardwareProfile) -> None:
    paths.settings.mkdir(parents=True, exist_ok=True)
    destination = _hardware_state_path(paths)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "mps_failed": profile.mps_failed,
                "validated_backend": profile.backend,
                "validated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def select_hardware(paths: RuntimePaths, *, force_cpu: bool = False) -> HardwareProfile:
    failed = mps_was_disabled(paths)
    return detect_hardware(force_cpu=force_cpu or failed, mps_failed=failed)
