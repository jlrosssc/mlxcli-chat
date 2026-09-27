#!/usr/bin/env python3
"""mlxcli-chat's chat window, built on PySide6/Qt.

Replaces the earlier Tkinter window: Tk's Aqua text renderer had real, hard-to-fix bugs on recent
macOS (a right-justified+background-colored paragraph could fail to redraw on scroll, making a
user's own earlier message appear to vanish; scrolling/selection in the transcript was reported as
"extremely unresponsive"). Qt's QTextEdit/QTextBrowser is a much more mature text renderer and does
not share that bug class.

Scope, honestly stated: this is a rewrite of the CHAT window only (this is mlxcli-chat, "almost
exclusively chat" by design -- see project notes). Kept: streaming chat, tool calls with an approval
dialog before each one runs, model picker (Gemma/Qwen3 via oMLX), warm-up on load, New Chat/Clear,
Import (attach a file's path so the model can read it), Settings (system prompt + generation
parameters), Word-document export, and the token/time-per-turn display (always visible -- Qt's
layout doesn't have the "must maximize the window" bug the old Tk toolbar had).
Deferred, not carried over from the general-purpose mlxgui coding tool this was adapted from:
backend switching (this build is locked to oMLX), RAG folder chat, tokenmax/tokenlocal routing, the
prompt-refine ("?") shortcut, repo/model update-check banners (those only apply to the TurboFieldfare
coding backends, which this build never uses), and saved-session load/save. None of that is wired
into the chat-only launcher today, so nothing here is a loss versus what a chat user was using.
"""
import html
import json
import os
import pathlib
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QPlainTextEdit,
    QPushButton, QComboBox, QLabel, QMessageBox, QFileDialog, QDialog, QFormLayout,
    QDoubleSpinBox, QSpinBox, QDialogButtonBox, QSizePolicy,
)

try:
    from docx_export import markdown_to_docx
except ImportError:
    markdown_to_docx = None


class InputBox(QPlainTextEdit):
    """A QPlainTextEdit consumes Return itself (inserting a newline) before a QShortcut on the same
    widget ever sees it, so a shortcut-based "Return to send" binding silently never fires -- this
    overrides keyPressEvent directly instead, which is the only reliable way to intercept it."""

    def __init__(self, on_send, parent=None):
        super().__init__(parent)
        self._on_send = on_send

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self._on_send()
            return
        super().keyPressEvent(event)

from mlxlib import (
    load_default_dir, MAX_FILE_CHARS, MAX_TOOL_STEPS, MAX_HISTORY_TURNS, MAX_CONTEXT_CHARS,
    all_tool_schemas, plugin_tools, is_code_request,
    should_auto_enable_agentic, requires_agentic_execution, execution_contract,
    tool_result_failed, resolve_output_path, normalize_tool_name, infer_command_cwd,
    backup_before_overwrite, find_project_notes, last_artifact_system_note, record_last_artifact,
    python_syntax_error, missing_local_imports,
    parse_bare_json_tool_call, parse_xml_tag_tool_call, parse_python_call_tool_call,
    parse_attr_tag_tool_call,
    load_model_settings, save_model_settings, MODEL_SETTING_BOUNDS,
    caffeinate_guard, server_busy_guard, log_error,
    web_search, load_host_aliases, keychain_get, request as ha_http_request, api as ha_http_api,
    prefer_default_model, drop_orphan_tool_messages,
)


def trim(messages):
    """Bound the history, then make sure no tool result is left without its call (see
    drop_orphan_tool_messages's docstring in mlxlib -- a long tool-heavy session getting the front of
    its history cut can otherwise leave an orphaned tool result, which the server rejects with a 400
    on every later turn)."""
    starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(starts) > MAX_HISTORY_TURNS:
        cut = starts[-MAX_HISTORY_TURNS]
        messages = [messages[0]] + messages[cut:]
    if sum(len(str(m.get("content") or "")) for m in messages) > MAX_CONTEXT_CHARS:
        system = messages[:1]
        kept = []
        total = sum(len(str(m.get("content") or "")) for m in system)
        for message in reversed(messages[1:]):
            size = len(str(message.get("content") or ""))
            if kept and total + size > MAX_CONTEXT_CHARS:
                break
            kept.append(message)
            total += size
        messages = system + list(reversed(kept))
    return drop_orphan_tool_messages(messages)

CODE_REQUEST_SYSTEM = (
    "This turn is a code/script request. Do not include hidden reasoning, planning, or analysis. "
    "Start with the final code block, shown directly in this response. After the code, include only "
    "requested output or one short usage note."
)


def request_messages_for_turn(messages, user_text):
    """Add a code-request system note for this turn only, when the request looks like a code/script
    ask. (The general mlxgui tool also folds in a RAG-folder's excerpts here; this chat build doesn't
    offer RAG-folder chat, so that half is left out.)"""
    if not is_code_request(user_text):
        return messages
    scoped = list(messages)
    if scoped and scoped[0].get("role") == "system":
        scoped[0] = {"role": "system", "content": scoped[0]["content"] + "\n\n" + CODE_REQUEST_SYSTEM}
    else:
        scoped.insert(0, {"role": "system", "content": CODE_REQUEST_SYSTEM})
    return scoped

SETTINGS = pathlib.Path.home() / ".omlx" / "settings.json"
SYSTEM_PROMPT_PATH = pathlib.Path.home() / ".omlx" / "mlx_system_prompt.txt"

DEFAULT_SYSTEM = (
    "You are a concise assistant running locally on the user's Mac.\n"
    "Do not reveal hidden reasoning, internal planning, or chain-of-thought.\n"
    "Treat the user's supplied text, numbers, filenames, and codes as authoritative literal data. "
    "Do not silently correct, normalize, truncate, expand, or substitute them unless explicitly asked.\n"
    "When a request refers to content 'below', 'above', or 'in the pasted list', verify that the content is "
    "actually present. If required input is missing, say exactly what is missing instead of guessing.\n"
    "When a request names a category or target (e.g. 'my photos', 'my documents', 'my files') without stating "
    "where it lives, ask which folder or path to use.\n"
)


# ---------------------------------------------------------------------------
# Pure server/model helpers -- unchanged logic from the earlier Tk build.
# This chat build is always the oMLX backend (see the launcher script), so
# the TurboFieldfare-coding-backend machinery from the general mlxgui tool
# is not needed here at all.
# ---------------------------------------------------------------------------

def load_cfg():
    url = os.environ.get("OMLX_URL", "http://localhost:8000")
    key = os.environ.get("OMLX_API_KEY", "")
    if not key and SETTINGS.exists():
        try:
            settings = json.loads(SETTINGS.read_text())
            key = settings.get("auth", {}).get("api_key", "")
            port = settings.get("server", {}).get("port", 8000)
            if "OMLX_URL" not in os.environ:
                url = f"http://localhost:{port}"
        except Exception:
            pass
    return url, key


def request(url, key, path, payload=None):
    return urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )


def api(url, key, path, payload=None):
    with urllib.request.urlopen(request(url, key, path, payload), timeout=900) as resp:
        return json.load(resp)


def server_up(url, key):
    try:
        api(url, key, "/v1/models")
        return True
    except Exception:
        return False


def list_models(url, key):
    data = api(url, key, "/v1/models")
    return prefer_default_model([m["id"] for m in data.get("data", [])])


def usage_counts(usage):
    usage = usage or {}
    return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def load_system_prompt():
    try:
        saved = SYSTEM_PROMPT_PATH.read_text().strip()
        if saved:
            return saved
    except Exception:
        pass
    return DEFAULT_SYSTEM


def save_system_prompt(text):
    SYSTEM_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYSTEM_PROMPT_PATH.write_text(text.strip() + "\n")


# ---------------------------------------------------------------------------
# Minimal markdown -> Qt rich-text HTML. Not a full CommonMark implementation --
# headings, bold, inline code, fenced code blocks, and bullet lists, which
# covers ordinary chat and document-drafting replies. Streaming shows raw
# text as it arrives (fast, no flicker); this renders the final text once a
# reply finishes.
# ---------------------------------------------------------------------------

def markdown_to_qt_html(text):
    lines = text.split("\n")
    out = []
    in_code = False
    code_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                code = "\n".join(code_lines)
                out.append(
                    '<pre style="background:#e9edf3;color:#111827;padding:8px 10px;'
                    'font-family:Menlo,monospace;font-size:13px;white-space:pre-wrap;">'
                    f"{html.escape(code)}</pre>"
                )
                code_lines = []
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            out.append("<p></p>")
            continue
        if stripped.startswith("### "):
            out.append(f'<h3 style="margin:10px 0 4px 0;">{_inline_md(stripped[4:])}</h3>')
        elif stripped.startswith("## "):
            out.append(f'<h2 style="margin:12px 0 6px 0;">{_inline_md(stripped[3:])}</h2>')
        elif stripped.startswith("# "):
            out.append(f'<h1 style="margin:14px 0 8px 0;">{_inline_md(stripped[2:])}</h1>')
        elif re.match(r"^[-*]\s+", stripped):
            out.append(f'<div style="margin-left:20px;">&bull; {_inline_md(re.sub(r"^[-*]\s+", "", stripped))}</div>')
        elif re.match(r"^\d+[.)]\s+", stripped):
            out.append(f'<div style="margin-left:20px;">{_inline_md(stripped)}</div>')
        else:
            out.append(f"<p style=\"margin:4px 0;\">{_inline_md(stripped)}</p>")
    if in_code and code_lines:
        code = "\n".join(code_lines)
        out.append(
            '<pre style="background:#e9edf3;color:#111827;padding:8px 10px;'
            'font-family:Menlo,monospace;font-size:13px;white-space:pre-wrap;">'
            f"{html.escape(code)}</pre>"
        )
    return "\n".join(out)


def _inline_md(text):
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r'<code style="background:#e9edf3;padding:1px 4px;">\1</code>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("mlxgui-chat")
        self.resize(920, 680)

        self.backend = "omlx"
        self.url, self.key = load_cfg()
        self.system_prompt = load_system_prompt()
        self.messages = [{"role": "system", "content": self.system_prompt}]
        notes_path, notes_text = find_project_notes()
        if notes_text:
            self.messages[0]["content"] += f"\n\nProject notes from {notes_path}:\n\n{notes_text}"
        artifact_note = last_artifact_system_note()
        if artifact_note:
            self.messages[0]["content"] += f"\n\n{artifact_note}"

        self.events = queue.Queue()
        self.model_ids = []
        self.totals = {"in": 0, "out": 0}
        self.last_user_text = ""
        self.busy = False
        self.cancel_requested = False
        self.turn_start_time = None
        self._assistant_pos = None
        self._assistant_raw = ""

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.drain_events)
        self.timer.start(60)

        threading.Thread(target=self.start_server_and_models, daemon=True).start()

    # ---- UI ----

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 8, 10, 8)

        top = QHBoxLayout()
        top.addWidget(QLabel("Model"))
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(260)
        top.addWidget(self.model_box)
        for label, handler in (
            ("New Chat", self.new_chat), ("Clear", self.clear_chat),
            ("Import", self.import_files), ("Settings", self.open_settings),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            top.addWidget(btn)
        top.addStretch(1)
        self.tokens_label = QLabel("tokens: in 0 / out 0")
        self.last_turn_label = QLabel("")
        self.last_turn_label.setStyleSheet("color:#2f6fed;")
        top.addWidget(self.last_turn_label)
        top.addWidget(self.tokens_label)
        layout.addLayout(top)

        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        self.chat.document().setDefaultStyleSheet("body{font-family:Helvetica;font-size:15px;}")
        layout.addWidget(self.chat, stretch=1)

        bottom = QHBoxLayout()
        self.input = InputBox(self.send)
        self.input.setFixedHeight(70)
        self.input.setPlaceholderText("Message mlxgui-chat... (Return to send, Shift+Return for a new line)")
        bottom.addWidget(self.input, stretch=1)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send)
        bottom.addWidget(self.send_button)
        layout.addLayout(bottom)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Starting")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        status_row.addWidget(self.status_label)
        layout.addLayout(status_row)

    # ---- server / models ----

    def status(self, text):
        self.events.put(("status", text))

    def start_server_and_models(self):
        for _ in range(60):
            if server_up(self.url, self.key):
                break
            time.sleep(2)
        else:
            self.status("Model server not reachable -- try reopening mlxgui-chat.")
            return
        try:
            models = list_models(self.url, self.key)
        except Exception as exc:
            self.status(f"Could not load models: {exc}")
            return
        self.events.put(("models", models))
        self.status("Ready")
        if models:
            threading.Thread(target=self.warm_up_model, args=(models[0],), daemon=True).start()

    def warm_up_model(self, model):
        try:
            api(self.url, self.key, "/v1/chat/completions",
                {"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1, "stream": False})
        except Exception:
            pass

    def selected_model_id(self):
        return self.model_box.currentData() or self.model_box.currentText()

    # ---- transcript rendering ----

    # All programmatic inserts below use a fresh QTextCursor(document), never self.chat.textCursor()
    # (the widget's own, visible/interactive cursor). Two real bugs came from that originally: (1)
    # every insert called ensureCursorVisible(), which snaps the view to the bottom unconditionally --
    # so scrolling up to read or select an earlier reply got undone by the next unrelated append (even
    # just sending a new message), which is exactly what "can't scroll" looks like from the outside;
    # (2) reassigning the widget's own cursor on every streamed token also hijacked any text the user
    # was actively selecting. Scrolling is now a plain, conditional scrollbar move -- it never touches
    # the user's own cursor/selection, and only follows new content if the user was already at the
    # bottom (the common case), never yanking them back down from a manual scroll.

    def _at_bottom(self):
        sb = self.chat.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def _scroll_to_bottom(self):
        sb = self.chat.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_html_block(self, html_block):
        was_at_bottom = self._at_bottom()
        cursor = QTextCursor(self.chat.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html_block)
        if was_at_bottom:
            self._scroll_to_bottom()

    def append_user_message(self, text):
        safe = html.escape(text).replace("\n", "<br>")
        block = (
            '<div style="margin:10px 0 10px 120px;background:#f2f1ef;color:#202020;'
            f'padding:10px 14px;">{safe}</div>'
        )
        self._append_html_block(block)

    def append_meta(self, text):
        safe = html.escape(text).replace("\n", "<br>")
        self._append_html_block(f'<div style="color:#667085;font-size:13px;">{safe}</div>')

    def start_assistant_block(self):
        self._append_html_block('<div style="color:#7a7a73;font-size:13px;margin-top:8px;">Assistant</div>')
        cursor = QTextCursor(self.chat.document())
        cursor.movePosition(QTextCursor.End)
        self._assistant_pos = cursor.position()
        self._assistant_raw = ""

    def append_assistant_chunk(self, piece):
        self._assistant_raw += piece
        was_at_bottom = self._at_bottom()
        cursor = QTextCursor(self.chat.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(piece)
        if was_at_bottom:
            self._scroll_to_bottom()

    def finalize_assistant_block(self):
        if self._assistant_pos is None or not self._assistant_raw:
            self._assistant_pos = None
            return
        was_at_bottom = self._at_bottom()
        cursor = QTextCursor(self.chat.document())
        cursor.setPosition(self._assistant_pos)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        rendered = markdown_to_qt_html(self._assistant_raw)
        cursor.insertHtml(f'<div style="background:#f7f7f5;padding:8px 12px;">{rendered}</div>')
        if was_at_bottom:
            self._scroll_to_bottom()
        self._assistant_pos = None
        self._assistant_raw = ""

    # ---- New Chat / Clear / Import / Settings ----

    def new_chat(self):
        self.reset_chat_state()

    def clear_chat(self):
        self.reset_chat_state()

    def reset_chat_state(self):
        self.messages = [{"role": "system", "content": self.system_prompt}]
        notes_path, notes_text = find_project_notes()
        if notes_text:
            self.messages[0]["content"] += f"\n\nProject notes from {notes_path}:\n\n{notes_text}"
        self.totals = {"in": 0, "out": 0}
        self.last_user_text = ""
        self.cancel_requested = False
        self.tokens_label.setText("tokens: in 0 / out 0")
        self.last_turn_label.setText("")
        self.chat.clear()
        self.input.clear()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files")
        if not paths:
            return
        refs = "\n".join(paths)
        current = self.input.toPlainText()
        prefix = (current + "\n") if current.strip() else ""
        self.input.setPlainText(f"{prefix}Files:\n{refs}\n")

    def open_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        form = QFormLayout(dlg)
        prompt_box = QPlainTextEdit(self.system_prompt)
        prompt_box.setMinimumSize(QSize(420, 160))
        form.addRow("System prompt:", prompt_box)

        settings = load_model_settings(self.backend)
        spinboxes = {}
        for key, (lo, hi) in MODEL_SETTING_BOUNDS.items():
            if key == "max_tokens":
                box = QSpinBox()
                box.setRange(int(lo), int(hi))
                box.setValue(int(settings[key]))
            else:
                box = QDoubleSpinBox()
                box.setDecimals(2)
                box.setRange(float(lo), float(hi))
                box.setSingleStep(0.05)
                box.setValue(float(settings[key]))
            form.addRow(key.replace("_", " ") + ":", box)
            spinboxes[key] = box

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.Accepted:
            self.system_prompt = prompt_box.toPlainText().strip() or DEFAULT_SYSTEM
            save_system_prompt(self.system_prompt)
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0]["content"] = self.system_prompt
            new_settings = {k: box.value() for k, box in spinboxes.items()}
            save_model_settings(self.backend, new_settings)

    # ---- send / tool approval / streaming (engine logic; adapted from the
    # earlier Tk build, which already isolated all of this behind a plain
    # queue + threading.Event, so it needed no real changes here) ----

    def request_tool_approval(self, description):
        event = threading.Event()
        result = {"approved": False, "cancelled": False}
        self.events.put(("approval", description, event, result))
        while not event.wait(0.1):
            if self.cancel_requested:
                result["cancelled"] = True
                return False
        return result["approved"]

    def execute_tool(self, name, args):
        name = str(name or "").strip().lower().replace(".", ":").replace("/", ":").rsplit(":", 1)[-1]
        if name == "run_command":
            command = args.get("command", "")
            try:
                mkdir_parts = shlex.split(command)
            except ValueError:
                mkdir_parts = []
            if mkdir_parts[:2] == ["mkdir", "-p"] and len(mkdir_parts) > 2:
                root = load_default_dir().resolve()
                targets = [pathlib.Path(part).expanduser().resolve(strict=False) for part in mkdir_parts[2:]]
                if all(t.is_relative_to(root) for t in targets) and all(t.exists() for t in targets):
                    return "exit_code=0\n(no action; requested directories already exist)"
            if not self.request_tool_approval(f"Run command:\n{command}"):
                return "User declined."
            try:
                cwd = infer_command_cwd(command)
                proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=180, cwd=cwd)
                output = (proc.stdout + proc.stderr).strip()[:MAX_FILE_CHARS]
                location = f"\nworking_directory={cwd}" if cwd else ""
                return f"exit_code={proc.returncode}{location}\n{output or '(no output)'}"
            except subprocess.TimeoutExpired:
                return "Command timed out."
        if name == "python_interpreter":
            code = args.get("code", "")
            if any(t in code for t in ("open(", "write_text(", "makedirs(", "mkdir(", "os.remove(", "unlink(")):
                return "Error: use write_file for file creation and run_command for execution; python_interpreter is disabled for filesystem writes."
            if not self.request_tool_approval(f"Run Python code ({len(code)} chars)"):
                return "User declined."
            try:
                proc = subprocess.run(["python3", "-c", code], capture_output=True, text=True, timeout=180)
                output = (proc.stdout + proc.stderr).strip()[:MAX_FILE_CHARS]
                return f"exit_code={proc.returncode}\n{output or '(no output)'}"
            except subprocess.TimeoutExpired:
                return "Python execution timed out."
        if name == "read_file":
            path = pathlib.Path(args.get("path", "")).expanduser()
            if path.is_dir():
                return f"Error: {path} is a directory, not a file. Use run_command with 'ls' or 'find' to see its contents."
            try:
                text = path.read_text(errors="replace")
                return text[:MAX_FILE_CHARS] + ("\n[truncated]" if len(text) > MAX_FILE_CHARS else "")
            except Exception as exc:
                return f"Error: {exc}"
        if name == "write_file":
            raw_path = args.get("path", "")
            content = args.get("content", args.get("text", ""))
            target = resolve_output_path(raw_path)
            old = ""
            if target.exists():
                try:
                    old = target.read_text(errors="replace")
                except Exception:
                    old = ""
            preview = f"Write {len(content)} chars to:\n{target}"
            if not self.request_tool_approval(preview):
                return "User declined."
            backup_path = backup_before_overwrite(target) if (old and old != content) else None
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                record_last_artifact(target, self.last_user_text)
                note = f" (previous version backed up to {backup_path})" if backup_path else ""
                result = f"Written: {target} ({target.stat().st_size} bytes).{note}"
                if target.suffix == ".py":
                    syntax_error = python_syntax_error(content)
                    if syntax_error:
                        return f"{result} Error: this file has a Python syntax error and will not run ({syntax_error})."
                    missing = missing_local_imports(target)
                    if missing:
                        return f"{result} Warning: this file imports {', '.join(missing)}, which has no matching file or package yet."
                return result
            except Exception as exc:
                return f"Error: {exc}"
        if name == "ha_api":
            target = str(args.get("host") or args.get("target") or "").strip().lower()
            method = str(args.get("method") or "GET").upper()
            api_path = args.get("path", "") or ("/api/states" if (args.get("domain") or args.get("state")) else "")
            data = args.get("data")
            entry = load_host_aliases().get(target)
            ha_cfg = None
            if entry:
                if entry.get("type") == "ha_api":
                    ha_cfg = entry
                elif entry.get("ha_api"):
                    ha_cfg = entry["ha_api"]
            if not ha_cfg:
                return f"Error: '{target}' is not a known ha_api-capable alias."
            token = keychain_get(target, ha_cfg.get("keychain_service", ""))
            if token is None:
                return f"Error: no stored HA token for alias '{target}'."
            if not self.request_tool_approval(f"Home Assistant API call:\n{target} {method} {api_path}"):
                return "User declined."
            try:
                base_url = ha_cfg["base_url"]
                if method == "GET":
                    result = ha_http_api(base_url, token, api_path)
                else:
                    req = ha_http_request(base_url, token, api_path, payload=data if data is not None else {})
                    req.get_method = lambda: method
                    with urllib.request.urlopen(req, timeout=30) as r:
                        result = json.load(r)
                text = json.dumps(result, indent=2)
                note = "" if len(text) <= MAX_FILE_CHARS else "\n[truncated]"
                return f"[source: {target}]\n" + text[:MAX_FILE_CHARS] + note
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:800]
                return f"HTTP {exc.code} from {target}: {detail}"
            except Exception as exc:
                return f"Error calling ha_api on {target}: {exc}"
        if name == "web_search":
            query = args.get("query", "")
            max_results = args.get("max_results", 5)
            if not str(query).strip():
                return "Error: web_search needs a non-empty 'query'."
            if not self.request_tool_approval(f"Web search:\n{query}\n(up to {max_results} results)"):
                return "User declined."
            try:
                results = web_search(query, max_results=max_results)
            except Exception as exc:
                return f"Error running web_search: {exc}"
            if not results:
                return f"[web_search: {query!r}] No results."
            lines = [f"[web_search: {query!r}] {len(results)} result(s):"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
            return "\n".join(lines)[:MAX_FILE_CHARS]
        if name in ("ssh_run", "ssh_read", "ssh_write"):
            return (f"'{name}' is not available in this chat app. Tell the user to use mlxcli "
                    f"(the terminal version) for SSH access instead.")
        plugin = plugin_tools().get(name)
        if plugin:
            if plugin["requires_approval"] and not self.request_tool_approval(f"{name}:\n{args}"):
                return "User declined."
            try:
                return plugin["run"](args)
            except Exception as exc:
                return f"Error running plugin tool '{name}': {exc}"
        return f"Unknown tool: {name}"

    def send(self):
        if self.busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        model = self.selected_model_id()
        if not model:
            QMessageBox.information(self, "No model", "Wait for models to load first.")
            return
        self.input.clear()
        self.last_user_text = text
        self.messages.append({"role": "user", "content": text})
        self.messages = trim(self.messages)
        self.cancel_requested = False
        self.append_user_message(text)
        self.start_assistant_block()
        self.turn_start_time = time.time()
        self.busy = True
        self.send_button.setEnabled(False)
        self.status_label.setText("Waiting for model response")
        threading.Thread(target=self.stream_reply, args=(model,), daemon=True).start()

    def stream_reply(self, model):
        with caffeinate_guard(), server_busy_guard(on_wait=self.status):
            self._stream_reply_body(model)

    def _stream_reply_body(self, model):
        working_messages = request_messages_for_turn(self.messages, self.last_user_text)
        agentic = should_auto_enable_agentic(self.last_user_text)
        execution_required = requires_agentic_execution(self.last_user_text)
        contract = execution_contract(self.last_user_text)
        tool_state = {"write": False, "run": False, "verify": False}
        seen_tool_calls = {}
        retried_with_tools = False
        if agentic:
            agentic_note = (
                "Agentic local-resource task: use tools for all file reads, writes, commands, and verification. "
                "Do not simulate tool calls or claim completion without successful tool results and exact path evidence."
            )
            if working_messages and working_messages[0].get("role") == "system":
                working_messages[0] = {"role": "system", "content": working_messages[0]["content"] + "\n\n" + agentic_note}
            else:
                working_messages.insert(0, {"role": "system", "content": agentic_note})
        effective_agentic = agentic
        model_settings = load_model_settings(self.backend)
        for _step in range(MAX_TOOL_STEPS):
            payload = {
                "model": model, "messages": working_messages,
                "max_tokens": model_settings["max_tokens"], "stream": True,
                "stream_options": {"include_usage": True},
                "temperature": model_settings["temperature"], "top_p": model_settings["top_p"],
                "top_k": model_settings["top_k"], "repetition_penalty": model_settings["repetition_penalty"],
                "tools": all_tool_schemas(),
            }
            if payload["model"].endswith("-fast"):
                payload["model"] = payload["model"][:-len("-fast")]
            parts, calls, usage = [], {}, {}
            stream_error = None
            try:
                with urllib.request.urlopen(request(self.url, self.key, "/v1/chat/completions", payload), timeout=900) as resp:
                    for rawline in resp:
                        if self.cancel_requested:
                            self.events.put(("canceled", "".join(parts)))
                            return
                        line = rawline.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            stream_error = chunk["error"]
                            break
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            parts.append(piece)
                            if not effective_agentic:
                                self.events.put(("append", piece))
                        for tool_call in delta.get("tool_calls") or []:
                            index = tool_call.get("index", 0)
                            slot = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                            slot["id"] = tool_call.get("id") or slot["id"]
                            function = tool_call.get("function") or {}
                            slot["name"] += function.get("name") or ""
                            slot["arguments"] += function.get("arguments") or ""
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace").strip()[:400]
                log_error("gui_chat_turn", f"HTTP {exc.code}: {detail}")
                self.events.put(("append", f"\n[error] HTTP {exc.code}: {detail}\n"))
                self.events.put(("done", "", usage))
                return
            except Exception as exc:
                if self.cancel_requested:
                    self.events.put(("canceled", "".join(parts)))
                else:
                    log_error("gui_chat_turn", f"{type(exc).__name__}: {exc}")
                    self.events.put(("append", f"\n[error] {exc}\n"))
                    self.events.put(("done", "", usage))
                return
            if stream_error:
                if not effective_agentic and not retried_with_tools:
                    retried_with_tools = True
                    effective_agentic = True
                    self.events.put(("status", "Server rejected an unexpected tool call; retrying with tools enabled"))
                    continue
                self.events.put(("append", f"\n[server error mid-generation: {stream_error}]\n"))
            tool_calls = [{"id": slot["id"] or f"gui_call_{index}", "type": "function",
                           "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                          for index, slot in sorted(calls.items())]
            content = "".join(parts)
            for parser in (parse_bare_json_tool_call, parse_xml_tag_tool_call,
                           parse_python_call_tool_call, parse_attr_tag_tool_call):
                if not tool_calls:
                    tool_calls = parser(content)
            if not tool_calls:
                if execution_required and _step == 0:
                    working_messages.append({"role": "assistant", "content": content})
                    working_messages.append({"role": "user", "content": (
                        "Execution required for this request. You did not call a tool. Use run_command, "
                        "read_file, or write_file now to perform the requested actions, then verify the "
                        "exact output paths before responding."
                    )})
                    continue
                unmet = [name for name, required in contract.items() if required and not tool_state.get(name)]
                if unmet and _step < MAX_TOOL_STEPS - 1:
                    working_messages.append({"role": "assistant", "content": content})
                    working_messages.append({"role": "user", "content": (
                        "Do not claim completion. Missing tool evidence for: " + ", ".join(unmet) + ". "
                        "Use the available tools and verify exact paths before responding."
                    )})
                    continue
                if unmet:
                    self.events.put(("append", f"\n[unverified: missing tool evidence for {', '.join(unmet)}]\n"))
                    self.events.put(("done", "", usage))
                    return
                if effective_agentic and content:
                    self.events.put(("append", content))
                self.events.put(("done", content, usage))
                return
            clean_content = "" if "call:" in content else content
            assistant_tool_message = {"role": "assistant", "content": clean_content or None, "tool_calls": tool_calls}
            working_messages.append(assistant_tool_message)
            self.events.put(("tool_history", assistant_tool_message))
            repeated_failure = False
            for call in tool_calls:
                call["function"]["name"] = normalize_tool_name(call["function"]["name"])
                try:
                    call_args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    call_args = {}
                self.events.put(("status", f"Running tool: {call['function']['name']}"))
                call_key = (call["function"]["name"], json.dumps(call_args, sort_keys=True, ensure_ascii=False))
                if call_key in seen_tool_calls:
                    result = seen_tool_calls[call_key]
                    if tool_result_failed(result):
                        repeated_failure = True
                else:
                    result = self.execute_tool(call["function"]["name"], call_args)
                    seen_tool_calls[call_key] = result
                if self.cancel_requested:
                    self.events.put(("canceled", content))
                    return
                if not tool_result_failed(result):
                    if call["function"]["name"] == "write_file":
                        tool_state["write"] = True
                    if call["function"]["name"] in {"run_command", "python_interpreter"}:
                        tool_state["run"] = True
                    if call["function"]["name"] == "read_file":
                        tool_state["verify"] = True
                working_messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                self.events.put(("tool_history", working_messages[-1]))
            if repeated_failure:
                self.events.put(("append", "\n[stopped: the model repeated an already-failed tool call]\n"))
                self.events.put(("done", "", usage))
                return
        self.events.put(("append", "\n[stopped: too many tool steps]\n"))
        self.events.put(("done", "", {}))

    # ---- event drain (Qt main-thread side of the worker-thread queue) ----

    def drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "approval":
                    _kind, description, approval_event, approval_result = event
                    if self.cancel_requested:
                        approval_result["approved"] = False
                    else:
                        answer = QMessageBox.question(self, "Approve local tool action", description,
                                                        QMessageBox.Yes | QMessageBox.No)
                        approval_result["approved"] = answer == QMessageBox.Yes
                    approval_event.set()
                elif kind == "tool_history":
                    self.messages.append(event[1])
                elif kind == "append":
                    self.append_assistant_chunk(event[1])
                elif kind == "status":
                    self.status_label.setText(event[1])
                elif kind == "models":
                    models = event[1]
                    self.model_ids = models
                    self.model_box.clear()
                    for mid in models:
                        self.model_box.addItem(mid, mid)
                elif kind == "done":
                    content, usage = event[1], event[2]
                    self.cancel_requested = False
                    if content:
                        self.finalize_assistant_block()
                        self.messages.append({"role": "assistant", "content": content})
                        if self._autosave_docx_requested() and markdown_to_docx is not None:
                            try:
                                self._save_docx_content(content)
                            except Exception as exc:
                                self.append_meta(f"[could not save Word document: {exc}]")
                    else:
                        self.finalize_assistant_block()
                    in_tokens, out_tokens = usage_counts(usage)
                    elapsed = (time.time() - self.turn_start_time) if self.turn_start_time else None
                    if elapsed and elapsed > 0 and out_tokens > 0:
                        run_time = f"{elapsed:.1f}s" if elapsed < 60 else f"{int(elapsed // 60)}m {int(elapsed % 60):02d}s"
                        self.last_turn_label.setText(f"last: {out_tokens / elapsed:.1f} tok/s · {run_time}")
                    self.turn_start_time = None
                    self.totals["in"] += in_tokens
                    self.totals["out"] += out_tokens
                    self.tokens_label.setText(f"tokens: in {self.totals['in']:,} / out {self.totals['out']:,}")
                    self.status_label.setText(f"Ready — last turn: in {in_tokens:,} / out {out_tokens:,}")
                    self.busy = False
                    self.send_button.setEnabled(True)
                elif kind == "canceled":
                    self.finalize_assistant_block()
                    self.busy = False
                    self.send_button.setEnabled(True)
                    self.status_label.setText("Stopped")
        except queue.Empty:
            pass

    # ---- Word export ----

    def _autosave_docx_requested(self):
        text = self.last_user_text.lower()
        wants_doc = any(t in text for t in ("word document", "docx", ".docx", "export as word", "save as word"))
        wants_save = any(t in text for t in ("save", "export", "create", "make", "generate"))
        return wants_doc and wants_save

    def _save_docx_content(self, content):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = load_default_dir() / f"mlxgui-chat-{stamp}.docx"
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown_to_docx(target, "mlxgui-chat Export", content)
        self.status_label.setText(f"Saved {target.name}")
        self.append_meta(f"[saved Word document to {target}]")


def main():
    app = QApplication(sys.argv)
    window = ChatWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
