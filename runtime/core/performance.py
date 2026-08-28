from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from typing import Any


def _windows_process_memory_bytes() -> tuple[int | None, int | None]:
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL

        value = Counters()
        value.cb = ctypes.sizeof(value)
        if get_process_memory_info(
            get_current_process(), ctypes.byref(value), value.cb
        ):
            return int(value.working_set_size), int(value.peak_working_set_size)
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        pass
    return None, None


def process_memory_bytes() -> tuple[int | None, int | None]:
    system = platform.system()
    if system == "Windows":
        return _windows_process_memory_bytes()
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if system != "Darwin":
            peak *= 1024
        current: int | None = None
        if system == "Linux":
            with open(f"/proc/{os.getpid()}/statm", encoding="ascii") as stream:
                statm = stream.read().split()
            current = int(statm[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        return current, peak
    except (ImportError, OSError, ValueError):
        return None, None


@dataclass(slots=True)
class PerformanceMetrics:
    values: dict[str, Any] = field(default_factory=dict)

    def milliseconds(self, key: str, seconds: float) -> None:
        self.values[key] = round(max(0.0, float(seconds)) * 1000.0, 3)

    def finish(self, *, audio_duration: float, total_seconds: float) -> dict[str, Any]:
        duration = max(0.0, float(audio_duration))
        elapsed = max(0.0, float(total_seconds))
        current_rss, peak_rss = process_memory_bytes()
        self.values.update(
            audio_duration_seconds=round(duration, 3),
            total_elapsed_ms=round(elapsed * 1000.0, 3),
            rtf=round(elapsed / duration, 6) if duration > 0 else None,
        )
        if current_rss is not None:
            self.values["process_rss_bytes"] = current_rss
        if peak_rss is not None:
            self.values["peak_rss_bytes"] = peak_rss
        return dict(self.values)
