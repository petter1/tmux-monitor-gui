#!/usr/bin/env bash
# Install tmux-monitor for the current user (no sudo, everything under ~/.local).
#
#   ./install.sh              install / update
#   ./install.sh --uninstall  remove again
set -euo pipefail
cd "$(dirname "$0")"

BIN_DIR="$HOME/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_DIR/applications"
LOCALE_DIR="$DATA_DIR/locale"
DOMAIN=tmux-monitor

CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-monitor"

if [[ ${1:-} == --uninstall ]]; then
    rm -fv "$BIN_DIR/tmux-monitor" "$BIN_DIR/tmux-monitor-archive" \
        "$APP_DIR/tmux-monitor.desktop" "$CONF_DIR/archive.tmux"
    echo "note: the session archive itself is kept: $(./tmux_monitor_archive.py dir)"
    echo "note: remove the source-file line for archive.tmux from your tmux.conf"
    rm -fv "$LOCALE_DIR"/*/LC_MESSAGES/$DOMAIN.mo
    command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" || true
    echo "tmux-monitor removed."
    exit 0
fi

# --- dependency check (warnings only)
python3 -c 'import PyQt6.QtWidgets' 2>/dev/null \
    || echo "WARNING: PyQt6 not found (Arch: python-pyqt6, Debian/Ubuntu: python3-pyqt6)"
command -v tmux >/dev/null || echo "WARNING: tmux not found"
command -v nvidia-smi >/dev/null || echo "note: nvidia-smi not found, GPU panel will be hidden"

# --- programs
install -Dm755 tmux_monitor.py "$BIN_DIR/tmux-monitor"
install -Dm755 tmux_monitor_archive.py "$BIN_DIR/tmux-monitor-archive"

# --- session archive: tmux snippet (your tmux.conf is not touched)
mkdir -p "$CONF_DIR"
"$BIN_DIR/tmux-monitor-archive" hook-conf --exe "$BIN_DIR/tmux-monitor-archive" > "$CONF_DIR/archive.tmux"

# --- translations
if command -v msgfmt >/dev/null; then
    ./i18n.sh compile "$LOCALE_DIR"
else
    echo "note: msgfmt (gettext) not found, UI stays English"
fi

# --- desktop entry (Exec needs an absolute path, ~/.local/bin is not always in PATH)
mkdir -p "$APP_DIR"
sed "s|^Exec=.*|Exec=$BIN_DIR/tmux-monitor|" tmux-monitor.desktop > "$APP_DIR/tmux-monitor.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" || true

echo "Installed: $BIN_DIR/tmux-monitor, $BIN_DIR/tmux-monitor-archive"
if ! grep -qs "tmux-monitor/archive.tmux" "$HOME/.tmux.conf" "${XDG_CONFIG_HOME:-$HOME/.config}/tmux/tmux.conf"; then
    echo
    echo "To enable the session archive, add this line to your tmux.conf:"
    echo "    source-file -q $CONF_DIR/archive.tmux"
    echo "and reload it:  tmux source-file ~/.tmux.conf   (or ~/.config/tmux/tmux.conf)"
fi
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "note: $BIN_DIR is not in your PATH" ;;
esac
