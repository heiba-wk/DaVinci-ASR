from __future__ import annotations

import sys
import os
from pathlib import Path

# The packaged entrypoint is hermetic. The launcher also clears these variables,
# and this second boundary prevents inherited user Python configuration.
os.environ.pop("PYTHONHOME", None)
os.environ.pop("PYTHONPATH", None)

# PyInstaller's runtime hook replaces freeze_support() so resource-tracker and
# spawned worker invocations are intercepted before the CLI parses their argv.
if getattr(sys, "frozen", False):
    import multiprocessing

    multiprocessing.freeze_support()

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
