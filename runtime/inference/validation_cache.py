from __future__ import annotations

import hashlib
import json
from typing import Any

from runtime.constants import RUNTIME_VERSION, SELF_TEST_REVISION
from runtime.core.paths import RuntimePaths
from runtime.core.types import HardwareProfile
from runtime.ipc.atomic import atomic_write_json, read_json


class SelfTestValidationCache:
    def __init__(self, paths: RuntimePaths) -> None:
        self.path = paths.settings / "self_test_validation.json"

    @staticmethod
    def key(
        hardware: HardwareProfile,
        model_identity: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            {
                "runtime_version": RUNTIME_VERSION,
                "self_test_revision": SELF_TEST_REVISION,
                "backend": hardware.backend,
                "device": hardware.device,
                "dtype": hardware.dtype,
                "models": model_identity,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_valid(
        self,
        hardware: HardwareProfile,
        model_identity: dict[str, Any],
    ) -> bool:
        try:
            value = read_json(self.path)
        except (FileNotFoundError, OSError, ValueError):
            return False
        return value.get("key") == self.key(hardware, model_identity)

    def record(
        self,
        hardware: HardwareProfile,
        model_identity: dict[str, Any],
    ) -> None:
        atomic_write_json(
            self.path,
            {
                "runtime_version": RUNTIME_VERSION,
                "self_test_revision": SELF_TEST_REVISION,
                "backend": hardware.backend,
                "key": self.key(hardware, model_identity),
            },
        )

    def invalidate(self) -> None:
        self.path.unlink(missing_ok=True)
