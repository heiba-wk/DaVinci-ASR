#!/bin/bash

set -euo pipefail

VERSION="${1:-1.0.0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WINDOWS_BUILD_ROOT="${DAVINCI_ASR_WINDOWS_BUILD_ROOT:-$HOME/DaVinciASR-Build/windows}"
DIST_ROOT="${DAVINCI_ASR_DIST_ROOT:-$HOME/DaVinciASR-Releases}"
DOCKER_IMAGE="${DAVINCI_ASR_INNO_IMAGE:-amake/innosetup@sha256:e542f3894b310342676629f6b7d04dd7f9b2f1e1df450b3a129a3b3c4dbae4b0}"
CPU_RUNTIME="$WINDOWS_BUILD_ROOT/windows-cpu-x64/dist/DaVinci ASR"
CUDA_RUNTIME="$WINDOWS_BUILD_ROOT/windows-cuda-x64/dist/DaVinci ASR"

for runtime in "$CPU_RUNTIME" "$CUDA_RUNTIME"; do
    if [[ ! -f "$runtime/DaVinci ASR.exe" ]]; then
        echo "Missing prebuilt Windows x64 Runtime: $runtime/DaVinci ASR.exe" >&2
        echo "Build both Windows Runtime variants on Windows before compiling the installer." >&2
        exit 1
    fi
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to run the pinned Inno Setup compiler on macOS." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker Desktop is not running." >&2
    exit 1
fi

BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/davinci-asr-windows-exe.XXXXXX")"
cleanup() {
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

STAGE_DIR="$BUILD_DIR/stage"
mkdir -p \
    "$STAGE_DIR/output" \
    "$STAGE_DIR/DaVinci ASR/config" \
    "$STAGE_DIR/DaVinci ASR/render_preset" \
    "$STAGE_DIR/build/windows/windows-cpu-x64/dist" \
    "$STAGE_DIR/build/windows/windows-cuda-x64/dist"

cp "$PROJECT_ROOT/DaVinci ASR/DaVinci ASR.lua" "$STAGE_DIR/DaVinci ASR/DaVinci ASR.lua"
cp "$PROJECT_ROOT/DaVinci ASR/config/setting.json" "$STAGE_DIR/DaVinci ASR/config/setting.json"
cp "$PROJECT_ROOT/DaVinci ASR/render_preset/render_to_wav.xml" "$STAGE_DIR/DaVinci ASR/render_preset/render_to_wav.xml"
cp "$PROJECT_ROOT/LICENSE" "$STAGE_DIR/LICENSE"
cp "$PROJECT_ROOT/THIRD_PARTY_NOTICES.md" "$STAGE_DIR/THIRD_PARTY_NOTICES.md"
cp "$SCRIPT_DIR/DaVinciASR.iss" "$STAGE_DIR/DaVinciASR.iss"
cp -R "$CPU_RUNTIME" "$STAGE_DIR/build/windows/windows-cpu-x64/dist/DaVinci ASR"
cp -R "$CUDA_RUNTIME" "$STAGE_DIR/build/windows/windows-cuda-x64/dist/DaVinci ASR"

if find "$STAGE_DIR" -name '.git' -o -name '.DS_Store' -o -name '._*' | grep -q .; then
    echo "Forbidden development or macOS metadata was found in the installer payload." >&2
    exit 1
fi
if find "$STAGE_DIR/build/windows" -type f \( \
    -name '*.safetensors' -o -name 'model.bin' -o -name '*.gguf' \
    -o -name '*.pt' -o -name '*.pth' \) | grep -q .; then
    echo "Model weights must not be embedded in the Windows installer." >&2
    exit 1
fi

docker run --rm \
    --network none \
    -v "$STAGE_DIR:/work" \
    "$DOCKER_IMAGE" \
    "/DAppVersion=$VERSION" \
    "/DSourceRoot=Z:\\work" \
    "/DOutputRoot=Z:\\work\\output" \
    "Z:\\work\\DaVinciASR.iss"

BUILT_EXE="$STAGE_DIR/output/DaVinciASRSetup.exe"
if [[ ! -f "$BUILT_EXE" ]]; then
    echo "Inno Setup did not produce DaVinciASRSetup.exe." >&2
    exit 1
fi

mkdir -p "$DIST_ROOT"
cp "$BUILT_EXE" "$DIST_ROOT/DaVinciASRSetup.exe"
echo "$DIST_ROOT/DaVinciASRSetup.exe"
