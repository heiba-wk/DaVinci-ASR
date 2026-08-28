from __future__ import annotations

import platform
from typing import Any

from runtime.constants import PROTOCOL_VERSION, RUNTIME_VERSION
from runtime.core.types import HardwareProfile


def diagnostics_payload(
    hardware: HardwareProfile,
    model_status: dict[str, str],
    *,
    last_job: str = "None",
    performance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "davinci_asr": RUNTIME_VERSION,
        "protocol": PROTOCOL_VERSION,
        "runtime": RUNTIME_VERSION,
        "os": platform.platform(terse=True),
        "architecture": hardware.architecture,
        "backend": hardware.backend,
        "gpu": hardware.name,
        "dtype": hardware.dtype,
        "asr_model": model_status.get("asr", "Not Installed"),
        "aligner_model": model_status.get("forced_aligner", "Not Installed"),
        "last_job": last_job,
        "mps_failed": hardware.mps_failed,
    }
    if performance:
        payload["performance"] = dict(performance)
    return payload


def format_diagnostics(value: dict[str, Any]) -> str:
    labels = (
        ("DaVinci ASR", "davinci_asr"),
        ("Protocol", "protocol"),
        ("Runtime", "runtime"),
        ("OS", "os"),
        ("Architecture", "architecture"),
        ("Backend", "backend"),
        ("GPU", "gpu"),
        ("Dtype", "dtype"),
        ("ASR Model", "asr_model"),
        ("Aligner Model", "aligner_model"),
        ("Last Job", "last_job"),
    )
    return "\n".join(f"{label}: {value.get(key, '')}" for label, key in labels)
