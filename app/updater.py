"""Self-update support for mlxcli-chat / mlxgui-chat (standard library only).

The installed version is app/VERSION. The newest published version is app/VERSION on GitHub's main branch.
To publish an update: bump app/VERSION, commit, push. Anyone on an older version then sees "Update available"
and can install it from Help > Check for Updates (GUI) or `mlxcli --self-update` (Terminal).
"""
import os
import pathlib
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = "jlrosssc/mlxcli-chat"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/main/app/VERSION"
INSTALL_URL = f"https://raw.githubusercontent.com/{REPO}/main/install.sh"
INSTALL_CMD = f"curl -fsSL {INSTALL_URL} | bash -s -- --models none"
HERE = pathlib.Path(__file__).resolve().parent


def installed_version():
    """Version of the code that is running, or "0" for an install that predates VERSION files."""
    try:
        return (HERE / "VERSION").read_text().strip() or "0"
    except Exception:
        return "0"


def latest_version(timeout=6):
    """Newest published version string, or None if GitHub can't be reached."""
    try:
        req = urllib.request.Request(f"{VERSION_URL}?t={int(time.time())}", headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            v = r.read().decode().strip()
        return v if v and len(v) < 32 and all(c.isdigit() or c == "." for c in v) else None
    except Exception:
        return None


def _key(v):
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def is_newer(latest, current):
    return _key(latest) > _key(current)


def check():
    """-> (state, current, latest): state is 'update', 'current' or 'offline'."""
    cur, new = installed_version(), latest_version()
    if new is None:
        return "offline", cur, None
    return ("update" if is_newer(new, cur) else "current"), cur, new


def desktop_app_path():
    return pathlib.Path(os.environ.get("MLXCLI_CHAT_DESKTOP", str(pathlib.Path.home() / "Desktop"))) / "mlxgui-chat.app"


def write_update_script(reopen_gui=True):
    """A double-clickable .command that runs the installer in Terminal (so progress is visible), then reopens the app."""
    app = shlex.quote(str(desktop_app_path()))
    lines = [
        "#!/bin/bash",
        "clear",
        'echo "Updating mlxcli-chat..."',
        "sleep 2   # let the chat window finish quitting",
        f"{INSTALL_CMD}",
        "status=$?",
        "echo",
        'if [ $status -ne 0 ]; then echo "The update stopped early (see the message above). Your old version is unchanged or can be re-run."',
        'else echo "Updated."',
    ]
    if reopen_gui:
        lines.append(f'  if [ -d {app} ]; then open {app}; fi')
    lines += ["fi", 'read -r -p "Press Return to close this window. " _', ""]
    path = pathlib.Path(tempfile.mkdtemp(prefix="mlxcli-chat-update-")) / "Update mlxcli-chat.command"
    path.write_text("\n".join(lines))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def launch_update_in_terminal(reopen_gui=True):
    """Open the update script in Terminal. The caller should quit the running app right after."""
    subprocess.Popen(["open", str(write_update_script(reopen_gui))])


def run_update_inline():
    """Terminal use: run the installer right here and return its exit code."""
    return subprocess.call(["bash", "-c", INSTALL_CMD])


if __name__ == "__main__":
    state, cur, new = check()
    print(f"installed {cur}, latest {new}, state {state}")
    sys.exit(0)
