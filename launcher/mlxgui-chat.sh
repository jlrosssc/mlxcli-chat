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
  # -j launches it hidden (no Welcome/main window, no Dock focus-steal) -- the user only asked to run its
  # background server, not to see its app window. If it shows a window anyway, hide it as a fallback (best
  # effort: needs Automation permission for Terminal/this app to control oMLX via System Events, and silently
  # does nothing if that isn't granted -- the server still starts either way).
  open -j -g -a oMLX >/dev/null 2>&1 || { alert "oMLX (the model server) isn't installed. Please run the installer again."; exit 1; }
  # The oMLX app installs a small `omlx` command on first launch; if it isn't there yet, use the one inside the app.
  OMLX_CLI="$OMLX_HOME/bin/omlx"
  if [ ! -x "$OMLX_CLI" ]; then
    for a in /Applications/oMLX.app "$HOME/Applications/oMLX.app"; do
      [ -x "$a/Contents/MacOS/omlx-cli" ] && { OMLX_CLI="$a/Contents/MacOS/omlx-cli"; break; }
    done
  fi
  [ -x "$OMLX_CLI" ] && "$OMLX_CLI" start --timeout 60 >>"$LOG" 2>&1
  for _ in $(seq 1 45); do
    up && break
    # Best effort, retried each pass in case its window appears late: needs Automation permission for this
    # app to control oMLX via System Events, and silently does nothing if that isn't granted -- the server
    # still starts either way, the user just also sees oMLX's window.
    osascript -e 'tell application "System Events" to set visible of process "oMLX" to false' >/dev/null 2>&1
    sleep 2
  done
  up || { alert "The model server didn't start. Open oMLX from your Applications folder once, then try again."; exit 1; }
fi

exec "$BASE/venv/bin/python" "$BASE/app/mlxgui.py" "$@" >>"$LOG" 2>&1
