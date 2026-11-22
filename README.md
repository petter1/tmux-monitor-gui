# tmux Monitor

A small desktop window (PyQt6) that lists your running **tmux sessions** with
their last line of output, plus live **NVIDIA GPU** usage. Double-click a
session to attach to it in a new terminal window.

Handy for keeping an eye on long jobs (installs, renders, model training,
TTS, …) that run detached in tmux.

![Screenshot](docs/screenshot.png)
<!-- TODO: add docs/screenshot.png -->

## Features

- All tmux sessions with uptime, attached/background status and the last
  non-empty output line (progress bars that redraw with `\r` show up correctly)
- Double-click opens `tmux attach` in a terminal: `$TERMINAL`, otherwise
  `konsole` → `gnome-terminal` → `xterm`
- GPU name, temperature, utilisation, VRAM and VRAM peak (via `nvidia-smi`);
  the GPU panel is hidden automatically when there is no NVIDIA GPU
- "Always on top" toggle
- English and German UI (gettext), follows the system locale

## Requirements

| Package | Arch Linux | Debian / Ubuntu | |
|---|---|---|---|
| Python 3.8+ with PyQt6 | `python-pyqt6` | `python3-pyqt6` | required |
| tmux | `tmux` | `tmux` | required |
| nvidia-smi | `nvidia-utils` | `nvidia-utils-<version>` | optional (GPU panel) |
| gettext | `gettext` | `gettext` | optional (German UI) |

```sh
# Arch
sudo pacman -S --needed python-pyqt6 tmux gettext   # + nvidia-utils if you have an NVIDIA GPU
```

## Install

```sh
git clone https://github.com/<user>/<repo>.git
cd <repo>
./install.sh
```

`install.sh` only writes to `~/.local` (no sudo):

- `~/.local/bin/tmux-monitor`
- `~/.local/share/applications/tmux-monitor.desktop` (app menu entry)
- `~/.local/share/locale/*/LC_MESSAGES/tmux-monitor.mo`

Uninstall with `./install.sh --uninstall`.

## Usage

```sh
tmux-monitor                  # or start "tmux Monitor" from the app menu
tmux-monitor --interval 5     # refresh every 5 s (default: 2)
tmux-monitor --lang de        # force German UI
TERMINAL=alacritty tmux-monitor
```

Without installing: `python3 tmux_monitor.py` (run `./i18n.sh compile` first
for the German UI).

Start a long job so it shows up in the monitor:

```sh
tmux new-session -d -s render 'blender -b scene.blend -a'
```

### Terminal

`$TERMINAL` may include arguments. The command is appended with `-e`, except
for terminals that need something else (`gnome-terminal --`, `kitty`,
`wezterm start --`). If `$TERMINAL` already ends with `-e`, `-x` or `--`,
nothing is added.

## Translations

Strings are marked as `_("context", "text")` (xgettext keyword `_:1c,2`).

```sh
./i18n.sh                                         # update po/tmux-monitor.pot and po/*.po, compile to ./locale
msginit -i po/tmux-monitor.pot -l fr -o po/fr.po  # add a language
```

## License

TBD
