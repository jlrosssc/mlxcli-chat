#!/bin/bash
# Desktop launcher for mlxgui-chat: make sure the oMLX model server is running, then open the chat window.
# @BASE@ and @OMLX_HOME@ are filled in by install.sh.
BASE="@BASE@"
OMLX_HOME="@OMLX_HOME@"
LOG="$OMLX_HOME/mlxgui-chat.log"
mkdir -p "$OMLX_HOME"

alert() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"mlxgui-chat\" with icon caution" >/dev/null 2>&1; }

[ -x "$BASE/venv/bin/python" ] || { alert "The chat app's files are missing. Please run the installer again."; exit 1; }

PORT="$("$BASE/venv/bin/python" - "$OMLX_HOME/settings.json" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("server", {}).get("port", 8000))
except Exception:
    print(8000)
PY
)"
up() { [ "$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://localhost:$PORT/v1/models")" != "000" ]; }

if ! up; then
  open -a oMLX >/dev/null 2>&1 || { alert "oMLX (the model server) isn't installed. Please run the installer again."; exit 1; }
  # The oMLX app installs a small `omlx` command on first launch; if it isn't there yet, use the one inside the app.
  OMLX_CLI="$OMLX_HOME/bin/omlx"
  if [ ! -x "$OMLX_CLI" ]; then
    for a in /Applications/oMLX.app "$HOME/Applications/oMLX.app"; do
      [ -x "$a/Contents/MacOS/omlx-cli" ] && { OMLX_CLI="$a/Contents/MacOS/omlx-cli"; break; }
    done
  fi
  [ -x "$OMLX_CLI" ] && "$OMLX_CLI" start --timeout 60 >>"$LOG" 2>&1
  for _ in $(seq 1 45); do up && break; sleep 2; done
  up || { alert "The model server didn't start. Open oMLX from your Applications folder once, then try again."; exit 1; }
fi

# MLXCLI_CHAT_ONLY tells mlxgui to skip its backend-picker dialog and use oMLX straight away -- this
# build is chat-only, so the user should only ever see the message window, never a server/backend choice.
export MLXCLI_CHAT_ONLY=1
exec "$BASE/venv/bin/python" "$BASE/app/mlxgui.py" "$@" >>"$LOG" 2>&1
