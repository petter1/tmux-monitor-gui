#!/usr/bin/env python3
"""Small tmux session and GPU monitor (PyQt6).

Double-clicking a session opens it in a new terminal window.
Terminal: $TERMINAL, otherwise konsole -> gnome-terminal -> xterm.
Without an NVIDIA GPU (no nvidia-smi) the GPU panel is hidden.
"""
import argparse
import gettext
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QLocale, Qt, QTimer, QTranslator, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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


LANGUAGES = [("", None), ("de", "Deutsch"), ("en", "English")]  # "" = system


def settings_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_ID / "settings.json"


def load_settings():
    try:
        return json.loads(settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    data = load_settings()
    data.update(changes)
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


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


# --- Session archive setup ----------------------------------------------------
HERE = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID
SNIPPET = CONFIG_DIR / "archive.tmux"
CONF_MARKER = "tmux-monitor/archive.tmux"
ARCHIVE_HOOKS = ("after-new-session", "after-new-window", "after-split-window")


def archive_tool():
    """Command (list) for tmux-monitor-archive, or None. A checkout wins over PATH."""
    local = HERE / "tmux_monitor_archive.py"
    if local.is_file():
        return [str(local)]
    exe = shutil.which("tmux-monitor-archive")
    return [exe] if exe else None


def tmux_conf_path():
    legacy = Path.home() / ".tmux.conf"
    if legacy.exists():
        return legacy
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tmux" / "tmux.conf"


def archive_enabled():
    try:
        return CONF_MARKER in tmux_conf_path().read_text()
    except OSError:
        return False


def write_snippet():
    """(Re)write the tmux snippet so it points at the current archive tool."""
    tool = archive_tool()
    if not tool:
        return False
    code, out = run(tool + ["hook-conf", "--exe", tool[0]])
    if code != 0:
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not SNIPPET.exists() or SNIPPET.read_text() != out:
        SNIPPET.write_text(out)
    return True


def set_archive_enabled(on):
    """Edit tmux.conf and apply the change to the running tmux server."""
    conf = tmux_conf_path()
    try:
        lines = conf.read_text().splitlines()
    except OSError:
        lines = []
    lines = [l for l in lines
             if CONF_MARKER not in l and l.strip() != "# tmux-monitor: session archive"]
    tool = archive_tool()
    if on:
        if not write_snippet():
            raise RuntimeError("tmux-monitor-archive not found")
        lines += ["# tmux-monitor: session archive",
                  f"source-file -q {shlex.quote(str(SNIPPET))}"]
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("\n".join(lines).strip("\n") + "\n" if lines else "")
    if on:
        run(["tmux", "source-file", str(SNIPPET)])
        run(tool + ["start-existing", "--exe", tool[0]])
    else:
        for hook in ARCHIVE_HOOKS:
            run(["tmux", "set-hook", "-gu", f"{hook}[42]"])


def archive_status():
    tool = archive_tool()
    if not tool:
        return None
    code, out = run(tool + ["status", "--json"])
    try:
        return json.loads(out) if code == 0 else None
    except ValueError:
        return None


def tilde(path):
    """Show paths below $HOME as ~/..."""
    p, home = str(path), str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def autostart_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{APP_ID}.desktop"


def launch_command():
    """How to start this app: the installed command, else this script."""
    installed = shutil.which(APP_ID)
    if installed and not (HERE / "tmux_monitor_archive.py").is_file():
        return shlex.quote(installed)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))}"


def autostart_enabled():
    return autostart_path().is_file()


def set_autostart(on):
    path = autostart_path()
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=tmux Monitor\n"
        "Comment=Watch tmux sessions and NVIDIA GPU usage\n"
        f"Exec={launch_command()}\n"
        "Icon=utilities-system-monitor\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    if not path.is_file() or path.read_text() != content:
        path.write_text(content)


def install_source():
    """Checkout dir with a PKGBUILD if we run from one and yay exists, else None."""
    if (HERE / "PKGBUILD").is_file() and shutil.which("yay"):
        return HERE
    return None


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
        self.table.cellDoubleClicked.connect(lambda row, _c: self.attach(row))
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.sessions = []
        self.empty = QLabel(_("sessions", "No tmux sessions."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sess_box = QGroupBox(_("sessions", "tmux sessions (double-click = open in new window)"))
        sl = QVBoxLayout(sess_box)
        sl.addWidget(self.table)
        sl.addWidget(self.empty)

        self.attach_btn = QPushButton()
        self.attach_btn.clicked.connect(self.toggle_attach)
        self.kill_btn = QPushButton(_("session button", "End session…"))
        self.kill_btn.clicked.connect(self.kill_session)
        btns = QHBoxLayout()
        btns.addWidget(self.attach_btn)
        btns.addWidget(self.kill_btn)
        btns.addStretch()
        sl.addLayout(btns)

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
        if load_settings().get("on_top_at_start", True):
            self.on_top.blockSignals(True)  # window is not shown yet
            self.on_top.setChecked(True)
            self.on_top.blockSignals(False)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.status = QLabel("")
        settings = QPushButton(_("footer", "Settings…"))
        settings.clicked.connect(self.show_settings)
        self.archive_hint = QPushButton()
        self.archive_hint.setFlat(True)
        self.archive_hint.setStyleSheet("color: #d08000;")
        self.archive_hint.clicked.connect(self.show_settings)
        self.archive_hint.hide()
        foot = QHBoxLayout()
        foot.addWidget(self.on_top)
        foot.addWidget(settings)
        foot.addWidget(self.archive_hint)
        foot.addStretch()
        foot.addWidget(self.status)

        # keep the snippet in sync with where the archive tool lives now
        if archive_enabled():
            write_snippet()
        if autostart_enabled():  # follow the app if it moved (e.g. now installed)
            set_autostart(True)
        self.archive_timer = QTimer(self)
        self.archive_timer.timeout.connect(self.check_archive)
        self.archive_timer.start(60_000)
        QTimer.singleShot(0, self.check_archive)

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
        # keep the selection on the same session, even if rows move
        current = self.selected()
        self.sessions = sessions
        self.table.blockSignals(True)
        self.table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            status = (_("session status", "attached") if s["attached"]
                      else _("session status", "in background"))
            values = [s["name"], fmt_age(s["age"]), status, s["last"]]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                self.table.setItem(row, col, item)
        self.table.clearSelection()
        names = [s["name"] for s in sessions]
        if current and current["name"] in names:
            self.table.selectRow(names.index(current["name"]))
        self.table.blockSignals(False)
        self.table.setVisible(bool(sessions))
        self.empty.setVisible(not sessions)
        self.update_buttons()

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

    def selected(self):
        """Selected session dict, or None."""
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self.sessions):
            return None
        return self.sessions[rows[0].row()]

    def update_buttons(self):
        s = self.selected()
        self.attach_btn.setEnabled(s is not None)
        self.kill_btn.setEnabled(s is not None)
        if s is not None and s["attached"]:
            self.attach_btn.setText(_("session button", "Detach"))
            self.attach_btn.setToolTip(
                _("session button", "Detach all clients; the session keeps running"))
        else:
            self.attach_btn.setText(_("session button", "Attach"))
            self.attach_btn.setToolTip(
                _("session button", "Open the session in a new terminal window"))

    def toggle_attach(self):
        s = self.selected()
        if s is None:
            return
        if s["attached"]:
            run(["tmux", "detach-client", "-s", f"={s['name']}"])
            self.refresh()
        else:
            self.attach(self.sessions.index(s))

    def kill_session(self):
        s = self.selected()
        if s is None:
            return
        answer = QMessageBox.question(
            self, _("dialog title", "End session"),
            _("dialog", "End tmux session “{name}”?\n"
                        "All programs running in it will be terminated.").format(name=s["name"]))
        if answer == QMessageBox.StandardButton.Yes:
            run(["tmux", "kill-session", "-t", f"={s['name']}"])
            self.refresh()

    def attach(self, row):
        self.open_in_terminal(self.sessions[row]["name"])

    def open_in_terminal(self, name):
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

    def check_archive(self):
        st = archive_status() if archive_enabled() else None
        if st and st.get("compaction_recommended"):
            self.archive_hint.setText(
                _("footer", "Archive: {size} – compact?").format(
                    size=human_size(st["raw_bytes"])))
            self.archive_hint.show()
        else:
            self.archive_hint.hide()

    def show_settings(self):
        SettingsDialog(self).exec()
        self.check_archive()


class SettingsDialog(QDialog):
    def __init__(self, monitor):
        super().__init__(monitor)
        self.monitor = monitor
        self.setWindowTitle(_("dialog title", "Settings"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)

        # --- Session archive
        box = QGroupBox(_("settings", "Session archive"))
        bl = QVBoxLayout(box)
        self.enable = QCheckBox(_("settings", "Archive the output of all tmux sessions"))
        self.enable.setChecked(archive_enabled())
        self.enable.toggled.connect(self.toggle_archive)
        hint = QLabel(_("settings",
                        "Adds one line to {conf} and applies it to the running tmux. "
                        "Running sessions are recorded from now on. The archive can "
                        "contain everything shown in your terminals.")
                      .format(conf=tilde(tmux_conf_path())))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.compact_btn = QPushButton(_("settings", "Compact"))
        self.compact_btn.setToolTip(_("settings",
                                      "Clean and gzip the logs of ended sessions"))
        self.compact_btn.clicked.connect(self.compact)
        open_btn = QPushButton(_("settings", "Open folder"))
        open_btn.clicked.connect(self.open_folder)
        row = QHBoxLayout()
        row.addWidget(self.compact_btn)
        row.addWidget(open_btn)
        row.addStretch()
        bl.addWidget(self.enable)
        bl.addWidget(hint)
        bl.addWidget(self.info)
        bl.addLayout(row)
        layout.addWidget(box)
        if not archive_tool():
            box.setEnabled(False)
            self.info.setText(_("settings", "tmux-monitor-archive not found."))

        # --- Start
        sbox = QGroupBox(_("settings", "Start"))
        sl = QVBoxLayout(sbox)
        self.autostart = QCheckBox(_("settings", "Start at login"))
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self.toggle_autostart)
        self.top_default = QCheckBox(_("settings", "Always on top at startup"))
        self.top_default.setChecked(load_settings().get("on_top_at_start", True))
        self.top_default.toggled.connect(lambda on: save_settings(on_top_at_start=on))
        sl.addWidget(self.autostart)
        sl.addWidget(self.top_default)
        layout.addWidget(sbox)

        # --- Language
        lbox = QGroupBox(_("settings", "Language"))
        ll = QHBoxLayout(lbox)
        self.lang = QComboBox()
        for code, label in LANGUAGES:
            self.lang.addItem(label or _("settings", "System default"), code)
        current = load_settings().get("lang") or ""
        self.lang.setCurrentIndex(max(0, self.lang.findData(current)))
        self.lang.currentIndexChanged.connect(self.change_language)
        self.restart_btn = QPushButton(_("settings", "Restart now"))
        self.restart_btn.clicked.connect(restart)
        self.restart_btn.hide()
        ll.addWidget(self.lang)
        ll.addWidget(self.restart_btn)
        ll.addStretch()
        layout.addWidget(lbox)

        # --- Installation (only when running from a checkout with PKGBUILD)
        self.src = install_source()
        if self.src:
            ibox = QGroupBox(_("settings", "Installation"))
            il = QVBoxLayout(ibox)
            code, out = run(["pacman", "-Q", "tmux-monitor-git"])
            installed = out.split()[1] if code == 0 and out.split() else None
            il.addWidget(QLabel(
                _("settings", "Installed version: {version}").format(version=installed)
                if installed else _("settings", "Not installed as a package.")))
            ib = QPushButton(_("settings", "Install / update with yay"))
            ib.setToolTip(_("settings",
                            "Builds the package from this folder in a tmux session "
                            "and opens it in a terminal (asks for your password)."))
            ib.clicked.connect(self.install)
            il.addWidget(ib, alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(ibox)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_info()

    def toggle_autostart(self, on):
        try:
            set_autostart(on)
        except OSError as e:
            QMessageBox.warning(self, _("dialog title", "Settings"), str(e))
            self.autostart.blockSignals(True)
            self.autostart.setChecked(autostart_enabled())
            self.autostart.blockSignals(False)

    def change_language(self, _index):
        save_settings(lang=self.lang.currentData() or None)
        self.restart_btn.show()

    def update_info(self):
        if not archive_tool():
            return
        st = archive_status()
        if not st:
            self.info.setText("")
            self.compact_btn.setEnabled(False)
            return
        text = _("settings", "Folder: {path}\n{entries} entries ({active} active), "
                             "raw {raw}, compacted {compacted}").format(
            path=tilde(st["archive"]), entries=st["entries"], active=st["active"],
            raw=human_size(st["raw_bytes"]), compacted=human_size(st["compacted_bytes"]))
        if st["compaction_recommended"]:
            text += "\n" + _("settings", "The archive is large – compacting is recommended.")
        self.info.setText(text)
        self.compact_btn.setEnabled(st["compactable_bytes"] > 0)

    def toggle_archive(self, on):
        try:
            set_archive_enabled(on)
        except (OSError, RuntimeError) as e:
            QMessageBox.warning(self, _("dialog title", "Session archive"), str(e))
            self.enable.blockSignals(True)
            self.enable.setChecked(archive_enabled())
            self.enable.blockSignals(False)
        self.update_info()

    def compact(self):
        tool = archive_tool()
        try:
            r = subprocess.run(tool + ["compact"], capture_output=True, text=True, timeout=600)
            msg = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            msg = str(e)
        self.update_info()
        QMessageBox.information(self, _("dialog title", "Session archive"), msg)

    def open_folder(self):
        st = archive_status()
        if st:
            path = Path(st["archive"])
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def install(self):
        name = "tmux-monitor-install"
        run(["tmux", "kill-session", "-t", f"={name}"])
        script = ("yay -Bi . ; echo; "
                  "read -rp 'Done – press Enter to close this window. '")
        code, _out = run(["tmux", "new-session", "-d", "-s", name, "-c", str(self.src),
                          "bash", "-c", script])
        if code == 0:
            self.monitor.open_in_terminal(name)
            self.accept()


def restart():
    """Restart the app (drops a --lang option so the saved language applies)."""
    argv, skip = [], False
    for arg in sys.argv[1:]:
        if skip:
            skip = False
        elif arg == "--lang":
            skip = True
        elif not arg.startswith("--lang="):
            argv.append(arg)
    os.execv(sys.executable, [sys.executable, sys.argv[0]] + argv)


def main():
    # --lang first, so that --help is already translated
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    pre_args, _rest = pre.parse_known_args()
    lang = pre_args.lang or load_settings().get("lang") or None
    setup_i18n(lang)

    parser = argparse.ArgumentParser(
        description=_("command line", "tmux session and GPU monitor (PyQt6)"))
    parser.add_argument("-i", "--interval", type=float, default=DEFAULT_INTERVAL,
                        metavar=_("command line metavar", "SEC"),
                        help=_("command line", "refresh interval in seconds (default: {default})")
                        .format(default=f"{DEFAULT_INTERVAL:g}"))
    parser.add_argument("--lang", metavar=_("command line metavar", "LANG"),
                        help=_("command line", "UI language, e.g. de or en (default: Settings, else system locale)"))
    args, qt_args = parser.parse_known_args()
    if args.interval <= 0:
        parser.error(_("command line", "--interval must be greater than 0"))

    app = QApplication([sys.argv[0]] + qt_args)
    app.setDesktopFileName(APP_ID)
    # Qt's own strings (standard buttons like "Close", "Yes", "No")
    qt_tr = QTranslator(app)
    locale = QLocale(lang) if lang else QLocale.system()
    if qt_tr.load(locale, "qtbase", "_",
                  QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt_tr)
    w = Monitor(interval=args.interval)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
