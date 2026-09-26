#!/bin/bash
# Double-click me to remove mlxcli-chat. Your downloaded models and the oMLX app are kept.
BASE="${MLXCLI_CHAT_HOME:-$HOME/Library/Application Support/mlxcli-chat}"
echo "This removes the mlxcli-chat app, its Python, the Desktop icon and the mlxcli-chat command."
echo "It does NOT remove oMLX or the downloaded models (delete those from ~/.omlx/models to free disk space)."
read -r -p "Continue? [y/N] " ans
[ "$ans" = "y" ] || [ "$ans" = "Y" ] || exit 0
rm -rf "$BASE" "${MLXCLI_CHAT_DESKTOP:-$HOME/Desktop}/mlxgui-chat.app" "${MLXCLI_CHAT_BIN:-$HOME/.local/bin}/mlxcli-chat"
echo "Removed."
read -r -p "Press Return to close this window. " _
