#!/bin/bash
# mlxcli-chat installer -- sets up a local chat / document-writing assistant on an Apple Silicon Mac.
#
# What it does (no admin password needed unless macOS asks to copy oMLX into /Applications):
#   1. checks the Mac (Apple Silicon, macOS 15+, RAM, free disk)
#   2. installs a private Python (with the Qt window toolkit) using micromamba -- nothing touches your system Python
#   3. installs oMLX, the local model server that runs the models
#   4. downloads the chat models (Gemma 3 12B and Qwen3 14B, 4-bit) from Hugging Face
#   5. puts an "mlxgui-chat" icon on your Desktop, and an `mlxcli-chat` terminal command in ~/.local/bin
#
# Usage:  ./install.sh [--models both|gemma|qwen|none] [--skip-omlx] [--no-desktop] [--yes]
# Or:     curl -fsSL https://raw.githubusercontent.com/jlrosssc/mlxcli-chat/main/install.sh | bash
set -euo pipefail

REPO="jlrosssc/mlxcli-chat"
OMLX_VERSION="0.6.4"                                   # the oMLX release mlxcli/mlxgui were tested with
PYTHON_VERSION="3.12"
GEMMA_REPO="mlx-community/gemma-3-12b-it-4bit"
QWEN_REPO="mlx-community/Qwen3-14B-4bit"

BASE="${MLXCLI_CHAT_HOME:-$HOME/Library/Application Support/mlxcli-chat}"
DESKTOP_DIR="${MLXCLI_CHAT_DESKTOP:-$HOME/Desktop}"
BIN_DIR="${MLXCLI_CHAT_BIN:-$HOME/.local/bin}"
OMLX_HOME="${MLXCLI_CHAT_OMLX_HOME:-$HOME/.omlx}"

ORIG_ARGS=("$@")   # kept so the curl|bash path can pass the same options to the downloaded copy
MODELS="both"; SKIP_OMLX=0; NO_DESKTOP=0; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODELS="${2:-both}"; shift 2 ;;
    --skip-omlx) SKIP_OMLX=1; shift ;;
    --no-desktop) NO_DESKTOP=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$MODELS" in both|gemma|qwen|none) ;; *) echo "--models must be both, gemma, qwen or none" >&2; exit 2 ;; esac

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mSetup stopped:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- if run through curl|bash (no app/ folder next to us), fetch the repo first -------------------
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -z "$SCRIPT_DIR" ] || [ ! -d "$SCRIPT_DIR/app" ]; then
  say "Downloading mlxcli-chat"
  TMP="$(mktemp -d)"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$TMP"
  SRC="$(echo "$TMP"/mlxcli-chat-*)"
  [ -d "$SRC/app" ] || die "could not download the mlxcli-chat files from GitHub."
  exec bash "$SRC/install.sh" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
fi
SRC="$SCRIPT_DIR"

# ---- 1. checks ---------------------------------------------------------------------------------
say "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || die "this only runs on a Mac."
[ "$(uname -m)" = "arm64" ] || die "this needs an Apple Silicon Mac (M1 or newer); Intel Macs can't run these models."
MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
[ "$MACOS_MAJOR" -ge 15 ] || die "macOS 15 (Sequoia) or newer is required; you have $(sw_vers -productVersion)."
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
info "macOS $(sw_vers -productVersion), ${RAM_GB} GB memory"
if [ "$RAM_GB" -lt 16 ]; then
  printf '    WARNING: this Mac has %s GB of memory. The 12-14B models want 16 GB or more and will be very slow here.\n' "$RAM_GB"
  if [ "$ASSUME_YES" -ne 1 ] && [ -t 0 ]; then
    read -r -p "    Continue anyway? [y/N] " ans; [ "$ans" = "y" ] || [ "$ans" = "Y" ] || exit 1
  fi
fi
NEED_GB=4; [ "$SKIP_OMLX" -eq 0 ] && NEED_GB=$((NEED_GB + 2))
case "$MODELS" in both) NEED_GB=$((NEED_GB + 17)) ;; gemma|qwen) NEED_GB=$((NEED_GB + 9)) ;; esac
FREE_GB=$(df -g "$HOME" | awk 'NR==2 {print $4}')
info "${FREE_GB} GB free disk (need about ${NEED_GB} GB)"
[ "$FREE_GB" -ge "$NEED_GB" ] || die "not enough free disk space: need about ${NEED_GB} GB, have ${FREE_GB} GB."

# ---- 2. app files + private Python -------------------------------------------------------------
say "Installing the app files"
mkdir -p "$BASE" "$BIN_DIR"
rm -rf "$BASE/app"
cp -R "$SRC/app" "$BASE/app"
cp "$SRC/assets/AppIcon.icns" "$BASE/AppIcon.icns"
info "$BASE/app"

say "Setting up Python (private copy, ~550 MB)"
# The chat window is built on Qt (PySide6), not Tk -- Tk's macOS text rendering had real bugs here
# (typing that vanished, then scrolling/selection that did too) that a mature, actively maintained
# renderer doesn't share. No admin rights needed; nothing touches any Python already on this Mac.
MM="$BASE/bin/micromamba"
if [ ! -x "$MM" ]; then
  mkdir -p "$BASE/bin"
  curl -fsSL -o "$MM" "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-osx-arm64" \
    || die "could not download the Python installer. Check your internet connection and run this again."
  chmod +x "$MM"
fi
export MAMBA_ROOT_PREFIX="$BASE/mamba"
rm -rf "$BASE/venv"
"$MM" create -y -q -p "$BASE/venv" -c conda-forge --override-channels \
  "python=$PYTHON_VERSION" psutil websockets huggingface_hub \
  || die "could not set up Python. Check your internet connection and run this again."
"$BASE/venv/bin/python" -m pip install --quiet pyside6-essentials \
  || die "could not install the window toolkit (PySide6). Check your internet connection and run this again."
"$BASE/venv/bin/python" -c "from PySide6.QtWidgets import QApplication; import psutil, huggingface_hub" \
  || die "the private Python was created but is missing a required package."
info "Python ready"

# ---- 3. oMLX (the model server) ----------------------------------------------------------------
find_omlx_app() {
  for d in /Applications "$HOME/Applications"; do [ -d "$d/oMLX.app" ] && { echo "$d/oMLX.app"; return 0; }; done
  return 1
}
if [ "$SKIP_OMLX" -eq 1 ]; then
  say "Skipping oMLX install (--skip-omlx)"
elif OMLX_APP="$(find_omlx_app)"; then
  say "oMLX is already installed"; info "$OMLX_APP"
else
  say "Installing oMLX ${OMLX_VERSION} (about 800 MB download)"
  if [ "$MACOS_MAJOR" -ge 26 ]; then DMG_NAME="oMLX-${OMLX_VERSION}-macos26-27.dmg"; else DMG_NAME="oMLX-${OMLX_VERSION}-macos15-sequoia.dmg"; fi
  DMG="$(mktemp -d)/$DMG_NAME"
  curl -fL --progress-bar -o "$DMG" "https://github.com/jundot/omlx/releases/download/v${OMLX_VERSION}/${DMG_NAME}" \
    || die "could not download oMLX. Check your internet connection and run the installer again."
  MNT="$(mktemp -d)"
  hdiutil attach -nobrowse -readonly -quiet -mountpoint "$MNT" "$DMG" || die "could not open the oMLX disk image."
  APP_SRC="$(find "$MNT" -maxdepth 1 -name '*.app' | head -1)"
  [ -n "$APP_SRC" ] || { hdiutil detach -quiet "$MNT" || true; die "the oMLX disk image had no app inside."; }
  if cp -R "$APP_SRC" /Applications/ 2>/dev/null; then OMLX_APP="/Applications/$(basename "$APP_SRC")"
  else mkdir -p "$HOME/Applications"; cp -R "$APP_SRC" "$HOME/Applications/"; OMLX_APP="$HOME/Applications/$(basename "$APP_SRC")"; fi
  hdiutil detach -quiet "$MNT" || true
  rm -f "$DMG"
  info "installed to $OMLX_APP"
fi

# ---- 4. models ---------------------------------------------------------------------------------
# Use the model folder oMLX is already configured for, else its default (models/ inside the oMLX folder).
MODEL_DIR="$("$BASE/venv/bin/python" - "$OMLX_HOME/settings.json" <<'PY'
import json, os, sys
d = os.path.join(os.path.dirname(sys.argv[1]), "models")
try:
    d = json.load(open(sys.argv[1])).get("model", {}).get("model_dir") or d
except Exception:
    pass
print(d)
PY
)"
download_model() {   # repo_id local_name
  say "Downloading $2 (this is the big step; you can leave it running)"
  "$BASE/venv/bin/python" - "$1" "$MODEL_DIR/$2" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])
PY
}
if [ "$MODELS" != "none" ]; then
  mkdir -p "$MODEL_DIR"
  case "$MODELS" in both|gemma) download_model "$GEMMA_REPO" "gemma-3-12b-it-4bit" ;; esac
  case "$MODELS" in both|qwen)  download_model "$QWEN_REPO"  "Qwen3-14B-4bit" ;; esac
fi
mkdir -p "$OMLX_HOME"
echo "omlx" > "$OMLX_HOME/mlx_backend.txt"                       # use the oMLX server, not a coding backend
case "$MODELS" in
  qwen) echo "Qwen3-14B" > "$OMLX_HOME/default_model.txt" ;;
  none) : ;;
  *)    echo "gemma-3-12b" > "$OMLX_HOME/default_model.txt" ;;   # Gemma is the default model
esac

# ---- 5. launchers ------------------------------------------------------------------------------
say "Creating the Desktop icon and terminal command"
cat > "$BIN_DIR/mlxcli-chat" <<EOF
#!/bin/bash
# Terminal chat. --ask makes it ask before it touches any file or runs anything.
exec "$BASE/venv/bin/python" "$BASE/app/mlxcli" --ask "\$@"
EOF
chmod +x "$BIN_DIR/mlxcli-chat"
info "$BIN_DIR/mlxcli-chat"

if [ "$NO_DESKTOP" -eq 0 ]; then
  APP="$DESKTOP_DIR/mlxgui-chat.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp "$BASE/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>mlxgui-chat</string>
  <key>CFBundleDisplayName</key><string>mlxgui-chat</string>
  <key>CFBundleIdentifier</key><string>com.jlrosssc.mlxgui-chat</string>
  <key>CFBundleExecutable</key><string>mlxgui-chat</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
  sed -e "s#@BASE@#$BASE#g" -e "s#@OMLX_HOME@#$OMLX_HOME#g" "$SRC/launcher/mlxgui-chat.sh" > "$APP/Contents/MacOS/mlxgui-chat"
  chmod +x "$APP/Contents/MacOS/mlxgui-chat"
  touch "$APP"
  info "$APP"
fi

say "All done"
[ "$NO_DESKTOP" -eq 0 ] && info "Double-click the blue chat icon named mlxgui-chat on your Desktop to start."
info "Terminal version: mlxcli-chat   (add $BIN_DIR to your PATH if the command isn't found)"
if [ "$SKIP_OMLX" -eq 0 ]; then
  info "The first time oMLX opens, macOS may ask permission to run it: click Open."
fi
