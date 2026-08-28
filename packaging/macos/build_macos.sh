#!/bin/bash
set -euo pipefail

MODE="signed"
case "${1:-}" in
    ""|--signed)
        ;;
    --unsigned)
        MODE="unsigned"
        ;;
    -h|--help)
        echo "Usage: $0 [--signed|--unsigned]"
        exit 0
        ;;
    *)
        echo "Usage: $0 [--signed|--unsigned]" >&2
        exit 2
        ;;
esac
if [[ $# -gt 1 ]]; then
    echo "Usage: $0 [--signed|--unsigned]" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LUA_SOURCE="$PROJECT_ROOT/DaVinci ASR/DaVinci ASR.lua"
RUNTIME_CONSTANTS="$PROJECT_ROOT/runtime/constants.py"
PROJECT_METADATA="$PROJECT_ROOT/pyproject.toml"
LOCK_FILE="$PROJECT_ROOT/requirements/locks/macos-arm64.txt"

VERSION="$(/usr/bin/sed -nE 's/^[[:space:]]*Config\.SCRIPT_VERSION[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$LUA_SOURCE" | /usr/bin/head -n 1)"
RUNTIME_VERSION="$(/usr/bin/sed -nE 's/^[[:space:]]*RUNTIME_VERSION[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$RUNTIME_CONSTANTS" | /usr/bin/head -n 1)"
PROJECT_VERSION="$(/usr/bin/sed -nE 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$PROJECT_METADATA" | /usr/bin/head -n 1)"
if [[ -z "$VERSION" || ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
    echo "Could not read a valid Config.SCRIPT_VERSION from $LUA_SOURCE" >&2
    exit 1
fi
if [[ "$RUNTIME_VERSION" != "$VERSION" || "$PROJECT_VERSION" != "$VERSION" ]]; then
    echo "Version mismatch: Lua=$VERSION Runtime=$RUNTIME_VERSION Project=$PROJECT_VERSION" >&2
    exit 1
fi

BUILD_BASE="${DAVINCI_ASR_BUILD_ROOT:-${TMPDIR:-/tmp}/davinci-asr-build}"
BUILD_ROOT="$BUILD_BASE/macos-arm64-$MODE"
DIST_ROOT="${DAVINCI_ASR_DIST_ROOT:-$PROJECT_ROOT/dist}"
STAGE_ROOT="$BUILD_ROOT/pkg-root"
PKG_SCRIPTS="$BUILD_ROOT/pkg-scripts"
COMPONENT_PLIST="$BUILD_ROOT/components.plist"
VENV="$BUILD_ROOT/venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_IDENTITY="${MACOS_APPLICATION_IDENTITY:-}"
INSTALLER_IDENTITY="${MACOS_INSTALLER_IDENTITY:-}"
NOTARY_PROFILE="${MACOS_NOTARY_PROFILE:-}"

case "$BUILD_ROOT" in
    ""|"/"|"$HOME"|"$PROJECT_ROOT"|"$PROJECT_ROOT"/*)
        echo "DAVINCI_ASR_BUILD_ROOT must be outside the Resolve Utility source tree." >&2
        exit 1
        ;;
esac

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "macos-arm64 must be built on Apple Silicon." >&2
    exit 1
fi

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT" "$DIST_ROOT"

if [[ "$MODE" == "signed" ]]; then
    if [[ ! -f "$LOCK_FILE" ]]; then
        echo "Missing hashed platform lock: $LOCK_FILE" >&2
        exit 1
    fi
    if [[ -z "$APP_IDENTITY" || -z "$INSTALLER_IDENTITY" || -z "$NOTARY_PROFILE" ]]; then
        echo "Set MACOS_APPLICATION_IDENTITY, MACOS_INSTALLER_IDENTITY, and MACOS_NOTARY_PROFILE." >&2
        exit 1
    fi
    "$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
    "$PYTHON_BIN" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes -r "$LOCK_FILE"
    BUILD_PYTHON="$VENV/bin/python"
    PYINSTALLER="$VENV/bin/pyinstaller"
else
    DEV_VENV="${DAVINCI_ASR_DEV_VENV:-$PROJECT_ROOT/.dev-runtime/venv}"
    BUILD_PYTHON="$DEV_VENV/bin/python"
    PYINSTALLER="$DEV_VENV/bin/pyinstaller"
    if [[ ! -x "$BUILD_PYTHON" || ! -x "$PYINSTALLER" ]]; then
        echo "Unsigned packaging requires the project development venv at $DEV_VENV" >&2
        exit 1
    fi
    "$BUILD_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
fi

if [[ -d "$PROJECT_ROOT/tests" ]]; then
    "$BUILD_PYTHON" -m unittest discover -s "$PROJECT_ROOT/tests" -v
fi

export PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-cache"
PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --onedir
    --windowed
    --name "DaVinci ASR"
    --osx-bundle-identifier com.heiba.davinci-asr.runtime
    --target-architecture arm64
    --osx-entitlements-file "$SCRIPT_DIR/entitlements.plist"
    --distpath "$BUILD_ROOT/dist"
    --workpath "$BUILD_ROOT/work"
    --specpath "$BUILD_ROOT"
    --collect-all transformers
    --collect-all jieba
    --collect-all nagisa
    --collect-all modelscope_hub
    --collect-all silero_vad
    --add-data "$PROJECT_ROOT/models/manifest.json:models"
)
if [[ "$MODE" == "signed" ]]; then
    PYINSTALLER_ARGS+=(--codesign-identity "$APP_IDENTITY")
fi
"$PYINSTALLER" "${PYINSTALLER_ARGS[@]}" "$PROJECT_ROOT/runtime/main.py"

RUNTIME_APP="$BUILD_ROOT/dist/DaVinci ASR.app"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$RUNTIME_APP/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$RUNTIME_APP/Contents/Info.plist"
mkdir -p "$RUNTIME_APP/Contents/Resources"
cp "$PROJECT_ROOT/LICENSE" "$RUNTIME_APP/Contents/Resources/LICENSE"
cp "$PROJECT_ROOT/THIRD_PARTY_NOTICES.md" "$RUNTIME_APP/Contents/Resources/THIRD_PARTY_NOTICES.md"

if [[ "$MODE" == "signed" ]]; then
    codesign --force --options runtime --timestamp \
        --entitlements "$SCRIPT_DIR/entitlements.plist" \
        --sign "$APP_IDENTITY" "$RUNTIME_APP"
else
    codesign --force --deep --timestamp=none --sign - "$RUNTIME_APP"
fi
codesign --verify --deep --strict --verbose=2 "$RUNTIME_APP"

"$RUNTIME_APP/Contents/MacOS/DaVinci ASR" \
    --data-root "$BUILD_ROOT/smoke-data" doctor --cpu

RUNTIME_DEST="$STAGE_ROOT/Library/Application Support/HEIBA/DaVinciASR/runtime"
SCRIPT_DEST="$STAGE_ROOT/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/DaVinci ASR"
mkdir -p \
    "$RUNTIME_DEST" \
    "$SCRIPT_DEST/audio_temp" \
    "$SCRIPT_DEST/config" \
    "$SCRIPT_DEST/render_preset" \
    "$SCRIPT_DEST/temp"
ditto "$RUNTIME_APP" "$RUNTIME_DEST/DaVinci ASR.app"
cp "$LUA_SOURCE" "$SCRIPT_DEST/DaVinci ASR.lua"
cp "$PROJECT_ROOT/DaVinci ASR/config/setting.json" "$SCRIPT_DEST/config/setting.json"
cp "$PROJECT_ROOT/DaVinci ASR/render_preset/render_to_asr_wav.xml" "$SCRIPT_DEST/render_preset/render_to_asr_wav.xml"
"$BUILD_PYTHON" -c 'import re, sys; source = open(sys.argv[1], encoding="utf-8").read(); match = re.search(r"Config\.DEFAULTS\s*=\s*\{(.*?)\n\s*\}", source, re.S); assert match and re.search(r"\bmax_chars\s*=\s*42\b", match.group(1)), "Packaged default max_chars must be 42"; assert match and re.search(r"\bui_language\s*=\s*\"en\"", match.group(1)), "Packaged default ui_language must be en"' "$SCRIPT_DEST/DaVinci ASR.lua"
mkdir -p "$PKG_SCRIPTS"
cp "$SCRIPT_DIR/pkg-scripts/preinstall" "$PKG_SCRIPTS/preinstall"
cp "$SCRIPT_DIR/pkg-scripts/postinstall" "$PKG_SCRIPTS/postinstall"
chmod 755 "$PKG_SCRIPTS/preinstall" "$PKG_SCRIPTS/postinstall"
xattr -cr "$STAGE_ROOT" "$PKG_SCRIPTS"

pkgbuild --analyze --root "$STAGE_ROOT" "$COMPONENT_PLIST"
/usr/libexec/PlistBuddy \
    -c "Set :0:BundleIsRelocatable false" \
    "$COMPONENT_PLIST"
EXPECTED_RUNTIME_BUNDLE_PATH="Library/Application Support/HEIBA/DaVinciASR/runtime/DaVinci ASR.app"
ANALYZED_RUNTIME_BUNDLE_PATH="$(plutil -extract 0.RootRelativeBundlePath raw -o - "$COMPONENT_PLIST")"
RUNTIME_IS_RELOCATABLE="$(plutil -extract 0.BundleIsRelocatable raw -o - "$COMPONENT_PLIST")"
if [[ "$ANALYZED_RUNTIME_BUNDLE_PATH" != "$EXPECTED_RUNTIME_BUNDLE_PATH" ]] || \
   [[ "$RUNTIME_IS_RELOCATABLE" != "false" ]]; then
    echo "Runtime component relocation was not disabled." >&2
    exit 1
fi

PKG_PATH="$DIST_ROOT/DaVinciASR-$VERSION-macos-arm64.pkg"
PKGBUILD_ARGS=(
    --root "$STAGE_ROOT"
    --component-plist "$COMPONENT_PLIST"
    --scripts "$PKG_SCRIPTS"
    --identifier com.heiba.davinci-asr
    --version "$VERSION"
    --install-location /
)
if [[ "$MODE" == "signed" ]]; then
    PKGBUILD_ARGS+=(--sign "$INSTALLER_IDENTITY")
fi
COPYFILE_DISABLE=1 pkgbuild "${PKGBUILD_ARGS[@]}" "$PKG_PATH"

PKG_AUDIT_ROOT="$BUILD_ROOT/pkg-audit"
pkgutil --expand-full "$PKG_PATH" "$PKG_AUDIT_ROOT"
PACKAGED_PLUGIN_MODELS_PATH="$(find "$PKG_AUDIT_ROOT" \( -path "*/DaVinci ASR/models" -o -path "*/DaVinci ASR/models/*" \) -print -quit)"
PACKAGED_SYSTEM_MODELS_PATH="$PKG_AUDIT_ROOT/Payload/Library/Application Support/HEIBA/DaVinciASR/models"
if [[ -n "$PACKAGED_PLUGIN_MODELS_PATH" || -e "$PACKAGED_SYSTEM_MODELS_PATH" || -L "$PACKAGED_SYSTEM_MODELS_PATH" ]]; then
    echo "PKG payload must not contain the user-managed models directory." >&2
    exit 1
fi

if [[ "$MODE" == "signed" ]]; then
    pkgutil --check-signature "$PKG_PATH"
    xcrun notarytool submit "$PKG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$PKG_PATH"
    xcrun stapler validate "$PKG_PATH"
    spctl --assess --type install --verbose=2 "$PKG_PATH"
    echo "Signed and notarized package: $PKG_PATH"
else
    echo "Unsigned local test package: $PKG_PATH"
fi
