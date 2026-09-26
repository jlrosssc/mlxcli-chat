# mlxcli-chat / mlxgui-chat

A private chat and document-writing assistant that runs **entirely on your Mac**. Nothing you type leaves the computer.

- **mlxgui-chat** — the desktop window. Click the icon on your Desktop and start typing.
- **mlxcli-chat** — the same assistant in the Terminal, for people who prefer it.

It is built from [mlxcli](https://github.com/jlrosssc/mlxcli), set up for everyday chat, writing and summarizing instead of coding. It uses [oMLX](https://github.com/jundot/omlx) to run the models on Apple Silicon.

## Quick install

Open **Terminal** (press Cmd+Space, type `Terminal`, press Return), paste this one line, and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/jlrosssc/mlxcli-chat/main/install.sh | bash
```

When it finishes, a blue chat icon named **mlxgui-chat** is on your Desktop. Prefer no Terminal at all? See [Install](#install-about-1530-minutes-mostly-downloading) below for the double-click option.

<p align="center"><img src="assets/icon_1024.png" width="128" alt="mlxgui-chat icon"></p>

## What you need

| | |
|---|---|
| Mac | Apple Silicon (M1, M2, M3, M4 or newer). Intel Macs are not supported. |
| macOS | 15 (Sequoia) or newer |
| Memory | **16 GB or more** (the models are 12–14 billion parameters) |
| Free disk | About 25 GB (about 15 GB for both models, 8 GB for one) |
| Internet | For the one-time download only. Afterwards it works offline. |

## Install (about 15–30 minutes, mostly downloading)

**Option A — double-click (no Terminal typing)**

1. On this page click the green **Code** button, then **Download ZIP**, and open the ZIP in your Downloads folder.
2. Right-click **Install mlxcli-chat.command** and choose **Open**, then **Open** again in the box that appears. (macOS asks this once for any downloaded script.)
3. A window shows the progress. When it says **All done**, close it.

**Option B — one line in Terminal**

```bash
curl -fsSL https://raw.githubusercontent.com/jlrosssc/mlxcli-chat/main/install.sh | bash
```

The installer:

1. checks your Mac has enough memory and disk space,
2. sets up its own private copy of Python (it does not touch any Python already on your Mac),
3. installs [oMLX](https://github.com/jundot/omlx) (the program that runs the models),
4. downloads the models, and
5. puts the **mlxgui-chat** icon on your Desktop.

If a download is interrupted, just run the installer again. It picks up where it left off.

Options: `--models gemma` or `--models qwen` (download only one, saves about 8 GB), `--skip-omlx`, `--no-desktop`, `--yes`.

## Use it

Double-click the blue chat icon named **mlxgui-chat** on your Desktop. The first launch takes a little longer while the model server starts. If macOS asks whether to open oMLX, click **Open**.

Terminal version: `mlxcli-chat` (if the command isn't found, add `~/.local/bin` to your PATH).

## The models

| Model | Size | Good at |
|---|---|---|
| **Gemma 3 12B (4-bit)** — the default | ~8 GB | writing, summarizing, rewriting, general chat |
| Qwen3 14B (4-bit) | ~8 GB | structured or analytical documents, step-by-step reasoning |

Gemma is what opens by default. Use the model menu at the top of the window to switch to Qwen. To change the default, edit `~/.omlx/default_model.txt` (it holds part of a model name, such as `gemma-3-12b` or `Qwen3-14B`).

Models come from [Hugging Face](https://huggingface.co/mlx-community) and are subject to their own licenses (Gemma: Google's Gemma terms; Qwen3: Apache 2.0).

## Safety

- Everything runs locally. The models never send your text anywhere.
- The window asks your permission before the assistant runs a command or code on your Mac, or changes a file. The Terminal command starts with the same confirmation switched on (`--ask`).

## Uninstall

Double-click **Uninstall mlxcli-chat.command**. This removes the app, its Python, the Desktop icon and the terminal command. It keeps oMLX and the downloaded models; delete `~/.omlx/models` to free that space.

## Troubleshooting

| Problem | Try |
|---|---|
| "The model server didn't start" | Open **oMLX** from your Applications folder once, then click the icon again. |
| It is very slow or the Mac stalls | Close other big apps. On an 8 GB Mac this will not work well. |
| The Desktop icon is missing | Run the installer again. |
| Something else | Look at `~/.omlx/mlxgui-chat.log`. |

## What is in this repository

- `install.sh`, `Install mlxcli-chat.command`, `Uninstall mlxcli-chat.command` — the installer and uninstaller
- `app/` — the chat programs (`mlxcli`, `mlxgui.py`, `mlxlib.py`, and helpers), from [mlxcli](https://github.com/jlrosssc/mlxcli)
- `launcher/` — the script behind the Desktop icon
- `assets/` — the icon and the script that draws it

Also published under the name **mlxgui-chat** — the [repository of that name](https://github.com/jlrosssc/mlxgui-chat) points here.
