#!/usr/bin/env python3
"""Small tmux session and GPU monitor (PyQt6).

Double-clicking a session opens it in a new terminal window.
Terminal: $TERMINAL, otherwise konsole -> gnome-terminal -> xterm.
Without an NVIDIA GPU (no nvidia-smi) the GPU panel is hidden.
"""
import argparse
import gettext
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

APP_ID = "tmux-monitor"
DEFAULT_INTERVAL = 2.0  # seconds
FALLBACK_TERMINALS = ["konsole", "gnome-terminal", "xterm"]
# How each terminal takes a command to run (default: "-e").
EXEC_FLAGS = {
    "gnome-terminal": ["--"],
    "kgx": ["--"],
    "kitty": [],
    "wezterm": ["start", "--"],
}

# --- i18n -----------------------------------------------------------------
# All UI strings go through _(context, text); xgettext keyword: _:1c,2
_translation = gettext.NullTranslations()


def _(context, message):
    return _translation.pgettext(context, message)


def locale_dirs():
    """Where compiled .mo files are searched, in order."""
    here = Path(__file__).resolve().parent
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return [here / "locale", data_home / "locale", None]  # None = system default


def setup_i18n(lang=None):
    global _translation
    languages = [lang] if lang else None
    for d in locale_dirs():
        t = gettext.translation(APP_ID, localedir=d, languages=languages, fallback=True)
        if isinstance(t, gettext.GNUTranslations):
            _translation = t
            return
    _translation = gettext.NullTranslations()  # English msgids as-is


# --- Helpers --------------------------------------------------------------
def find_terminal():
    """Terminal command as a list, or None. $TERMINAL takes precedence."""
    env = os.environ.get("TERMINAL", "").strip()
    if env:
        argv = shlex.split(env)
        if argv and shutil.which(argv[0]):
            return argv
    for name in FALLBACK_TERMINALS:
        if shutil.which(name):
            return [name]
    return None


def terminal_command(term, cmd):
    """Full command line that runs cmd inside terminal term."""
    if term[-1] in ("-e", "-x", "--"):  # $TERMINAL already ends with the flag
        return term + cmd
    flags = EXEC_FLAGS.get(os.path.basename(term[0]), ["-e"])
    return term + flags + cmd


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def tmux_sessions():
    code, out = run(["tmux", "ls", "-F",
                     "#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_created}"])
    if code != 0:
        return []
    sessions = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, wins, attached, created = parts
        sessions.append({
            "name": name,
            "windows": wins,
            "attached": attached != "0",
            "age": time.time() - int(created),
            "last": last_line(name),
        })
    return sessions


def last_line(session):
    """Last non-empty line of the active pane (shows e.g. progress in %)."""
    # "=name:" = exactly this session, its active window/pane
    code, out = run(["tmux", "capture-pane", "-p", "-t", f"={session}:"])
    if code != 0:
        return ""
    # Progress bars overwrite lines with \r: keep only the last part
    lines = [l.split("\r")[-1].strip() for l in out.splitlines()]
    lines = [l for l in lines if l]
    return lines[-1] if lines else ""


def gpu_info():
    code, out = run(["nvidia-smi",
                     "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                     "--format=csv,noheader,nounits"])
    if code != 0 or not out.strip():
        return None
    name, *vals = [p.strip() for p in out.splitlines()[0].split(",")]

    def num(v):
        try:
            return int(round(float(v)))
        except ValueError:  # e.g. "[N/A]"
            return 0

    util, used, total, temp = (num(v) for v in vals)
    return {"name": name, "util": util, "used": used, "total": total, "temp": temp}


def fmt_age(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --- GUI ------------------------------------------------------------------
class Monitor(QWidget):
    def __init__(self, interval=DEFAULT_INTERVAL):
        super().__init__()
        self.terminal = find_terminal()
        self.has_gpu = shutil.which("nvidia-smi") is not None
        self.setWindowTitle(_("window", "tmux Monitor"))
        self.resize(640, 360)
        self.peak_vram = 0

        # --- Sessions
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            _("session table header", "Session"),
            _("session table header", "Uptime"),
            _("session table header", "Status"),
            _("session table header", "Last output"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        hdr = self.table.horizontalHeader()
        for i in range(3):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self.open_session)
        self.empty = QLabel(_("sessions", "No tmux sessions."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sess_box = QGroupBox(_("sessions", "tmux sessions (double-click = open in new window)"))
        sl = QVBoxLayout(sess_box)
        sl.addWidget(self.table)
        sl.addWidget(self.empty)

        # --- GPU
        self.gpu_name = QLabel("–")
        self.util_bar = QProgressBar()
        self.vram_bar = QProgressBar()
        self.peak_label = QLabel(_("gpu", "Peak: –"))
        self.temp_label = QLabel("–")
        reset = QPushButton(_("gpu button", "Reset peak"))
        reset.clicked.connect(self.reset_peak)

        self.gpu_box = gpu_box = QGroupBox(_("gpu", "GPU"))
        gpu_box.setVisible(self.has_gpu)
        gl = QGridLayout(gpu_box)
        gl.addWidget(self.gpu_name, 0, 0, 1, 2)
        gl.addWidget(self.temp_label, 0, 2)
        gl.addWidget(QLabel(_("gpu", "Utilisation")), 1, 0)
        gl.addWidget(self.util_bar, 1, 1, 1, 2)
        gl.addWidget(QLabel(_("gpu", "VRAM")), 2, 0)
        gl.addWidget(self.vram_bar, 2, 1, 1, 2)
        gl.addWidget(self.peak_label, 3, 0, 1, 2)
        gl.addWidget(reset, 3, 2)

        # --- Footer
        self.on_top = QCheckBox(_("footer", "Always on top"))
        self.on_top.toggled.connect(self.set_on_top)
        self.status = QLabel("")
        foot = QHBoxLayout()
        foot.addWidget(self.on_top)
        foot.addStretch()
        foot.addWidget(self.status)

        root = QVBoxLayout(self)
        root.addWidget(sess_box, 1)
        root.addWidget(gpu_box)
        root.addLayout(foot)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(100, int(interval * 1000)))
        self.refresh()

    def refresh(self):
        sessions = tmux_sessions()
        self.table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            status = (_("session status", "attached") if s["attached"]
                      else _("session status", "in background"))
            values = [s["name"], fmt_age(s["age"]), status, s["last"]]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                self.table.setItem(row, col, item)
        self.table.setVisible(bool(sessions))
        self.empty.setVisible(not sessions)

        g = gpu_info() if self.has_gpu else None
        self.gpu_box.setVisible(g is not None)
        if g is not None:
            self.peak_vram = max(self.peak_vram, g["used"])
            self.gpu_name.setText(g["name"])
            self.temp_label.setText(f"{g['temp']} °C")
            self.util_bar.setValue(g["util"])
            self.util_bar.setFormat(f"{g['util']} %")
            self.vram_bar.setMaximum(g["total"])
            self.vram_bar.setValue(g["used"])
            self.vram_bar.setFormat(f"{g['used']} / {g['total']} MiB (%p %)")
            self.peak_label.setText(
                _("gpu", "Peak: {mib} MiB").format(mib=self.peak_vram))
        self.status.setText(
            _("footer", "updated {time}").format(time=time.strftime("%H:%M:%S")))

    def reset_peak(self):
        self.peak_vram = 0
        self.refresh()

    def open_session(self, row, _col):
        name = self.table.item(row, 0).text()
        if not self.terminal:
            self.status.setText(_("status message", "No terminal found (set $TERMINAL)"))
            return
        # "=" makes tmux match the session name exactly
        cmd = terminal_command(self.terminal, ["tmux", "attach", "-t", f"={name}"])
        try:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.status.setText(
                _("status message", "Failed to start terminal: {error}").format(error=e))

    def set_on_top(self, on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()


def main():
    # --lang first, so that --help is already translated
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    pre_args, _rest = pre.parse_known_args()
    setup_i18n(pre_args.lang)

    parser = argparse.ArgumentParser(
        description=_("command line", "tmux session and GPU monitor (PyQt6)"))
    parser.add_argument("-i", "--interval", type=float, default=DEFAULT_INTERVAL,
                        metavar=_("command line metavar", "SEC"),
                        help=_("command line", "refresh interval in seconds (default: {default})")
                        .format(default=f"{DEFAULT_INTERVAL:g}"))
    parser.add_argument("--lang", metavar=_("command line metavar", "LANG"),
                        help=_("command line", "UI language, e.g. de or en (default: system locale)"))
    args, qt_args = parser.parse_known_args()
    if args.interval <= 0:
        parser.error(_("command line", "--interval must be greater than 0"))

    app = QApplication([sys.argv[0]] + qt_args)
    app.setDesktopFileName(APP_ID)
    w = Monitor(interval=args.interval)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
