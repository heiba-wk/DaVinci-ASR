from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    models: Path
    ipc: Path
    logs: Path
    cache: Path
    settings: Path
    temp: Path

    @classmethod
    def discover(
        cls,
        override: str | Path | None = None,
        models_override: str | Path | None = None,
    ) -> "RuntimePaths":
        if override is not None:
            root = Path(override).expanduser().resolve()
        elif platform.system() == "Windows":
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise RuntimeError("LOCALAPPDATA is unavailable")
            root = Path(local) / "HEIBA" / "DaVinciASR"
        elif platform.system() == "Darwin":
            root = Path.home() / "Library" / "Application Support" / "HEIBA" / "DaVinciASR"
        else:
            root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "HEIBA" / "DaVinciASR"
        models = (
            Path(models_override).expanduser().resolve()
            if models_override is not None
            else root / "models"
        )
        return cls(
            root=root,
            models=models,
            ipc=root / "ipc",
            logs=root / "logs",
            cache=root / "cache",
            settings=root / "settings",
            temp=root / "temp",
        )

    def ensure(self) -> None:
        for path in (self.root, self.models, self.ipc, self.logs, self.cache, self.settings, self.temp):
            path.mkdir(parents=True, exist_ok=True)
