#!/bin/bash
# Double-click me to install mlxcli-chat.
cd "$(dirname "$0")" || exit 1
clear
echo "mlxcli-chat installer"
echo "---------------------"
bash ./install.sh
status=$?
echo
if [ $status -ne 0 ]; then echo "The installer stopped early (see the message above)."; fi
read -r -p "Press Return to close this window. " _
