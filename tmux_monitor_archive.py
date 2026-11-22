#!/usr/bin/env python3
"""tmux session archive (companion to tmux-monitor).

A tmux hook pipes every pane's output into a raw log file. This tool lists,
reads (cleaned of escape sequences) and compacts those logs.

    tmux-monitor-archive hook-conf      print the tmux config snippet
    tmux-monitor-archive status         size, counts, compaction advice
    tmux-monitor-archive list           all archived panes
    tmux-monitor-archive show NAME      cleaned text of a log (file or session name)
    tmux-monitor-archive compact        clean + gzip logs of ended sessions
    tmux-monitor-archive dir            print the archive directory

Archive: $TMUX_MONITOR_ARCHIVE or ${XDG_STATE_HOME:-~/.local/state}/tmux-monitor/archive
"""
import argparse
import gzip
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_THRESHOLD_MB = 200
HEADER_PREFIX = b"### tmux-monitor-archive "


def archive_dir():
    env = os.environ.get("TMUX_MONITOR_ARCHIVE")
    if env:
        return Path(env).expanduser()
    state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    return Path(state) / "tmux-monitor" / "archive"


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:60] or "session"


# --- writing (called from tmux pipe-pane) ----------------------------------
def cmd_log(a):
    """Read a pane's output from stdin and append it to its raw log."""
    d = archive_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(int(a.created)).strftime("%Y-%m-%d_%H%M%S")
    path = d / f"{stamp}_{safe(a.session)}_{a.pane.lstrip('%')}.log"
    command = a.command
    try:  # tmux reports the start command shell-quoted
        parts = shlex.split(command)
        if len(parts) == 1:
            command = parts[0]
    except ValueError:
        pass
    meta = {"session": a.session, "created": int(a.created), "pane": a.pane,
            "cwd": a.cwd, "command": command, "logging_since": int(time.time())}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.write(fd, HEADER_PREFIX + json.dumps(meta, ensure_ascii=False).encode() + b"\n")
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        os.write(fd, chunk)
    os.close(fd)


# --- reading -----------------------------------------------------------------
ANSI = re.compile(rb"""
    \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)     # OSC ... BEL/ST
  | \x1b[P^_X][^\x1b]*\x1b\\              # DCS/PM/APC/SOS ... ST
  | \x1b\[[0-?]*[ -/]*[@-~]               # CSI
  | \x1b[()][0-9A-Za-z]                   # charset
  | \x1b[ -/]*[0-~]                       # other ESC sequences
""", re.VERBOSE)
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(raw: bytes) -> str:
    """Raw terminal output -> readable text (progress redraws collapsed)."""
    text = ANSI.sub(b"", raw).decode("utf-8", errors="replace")
    out, prev = [], None
    for line in text.split("\n"):
        line = line.rstrip("\r").split("\r")[-1]       # \r redraws: keep the last state
        while "\b" in line:                             # backspace
            line = re.sub(r"[^\b]\b", "", line, count=1).lstrip("\b")
        line = CTRL.sub("", line).rstrip()
        if line == prev and line:                       # collapse repeated lines
            continue
        out.append(line)
        prev = line
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def read_entry(path: Path):
    """Metadata of a log (raw .log or compacted .txt.gz)."""
    opener = gzip.open if path.suffix == ".gz" else open
    meta = {}
    with opener(path, "rb") as f:
        first = f.readline()
    if first.startswith(HEADER_PREFIX):
        try:
            meta = json.loads(first[len(HEADER_PREFIX):])
        except ValueError:
            pass
    st = path.stat()
    return {
        "file": path.name,
        "path": str(path),
        "session": meta.get("session", path.name),
        "created": meta.get("created"),
        "pane": meta.get("pane"),
        "cwd": meta.get("cwd"),
        "command": meta.get("command"),
        "modified": int(st.st_mtime),
        "bytes": st.st_size,
        "compacted": path.suffix == ".gz",
    }


def live_panes():
    """{(session_created, pane_id)} of panes currently being logged."""
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_created} #{pane_id} #{pane_pipe}"],
            capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    live = set()
    for line in out.splitlines():
        created, pane, pipe = line.split()
        if pipe == "1":
            live.add((int(created), pane))
    return live


def entries():
    d = archive_dir()
    if not d.is_dir():
        return []
    live = live_panes()
    result = []
    for p in sorted(list(d.glob("*.log")) + list(d.glob("*.txt.gz"))):
        e = read_entry(p)
        e["active"] = not e["compacted"] and (e["created"], e["pane"]) in live
        result.append(e)
    return result


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"


def cmd_list(a):
    es = entries()
    if a.json:
        print(json.dumps(es, indent=2, ensure_ascii=False))
        return
    if not es:
        print(f"archive empty ({archive_dir()})")
        return
    for e in es:
        state = "active" if e["active"] else ("compacted" if e["compacted"] else "ended")
        print(f"{fmt_ts(e['created'])}  {e['session']:<20} {state:<9} {human(e['bytes']):>9}  "
              f"{e['command'] or ''}  [{e['file']}]")


def resolve(name):
    """File name, path or session name -> list of paths (newest session match)."""
    p = Path(name)
    if p.is_file():
        return [p]
    d = archive_dir()
    if (d / name).is_file():
        return [d / name]
    matches = [e for e in entries() if e["session"] == name]
    if not matches:
        sys.exit(f"no archive entry for '{name}' (see: tmux-monitor-archive list)")
    newest = max(e["created"] or 0 for e in matches)
    return [Path(e["path"]) for e in matches if (e["created"] or 0) == newest]


def text_of(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            body = f.read()
        return body.split("\n", 1)[1] if body.startswith(HEADER_PREFIX.decode()) else body
    raw = path.read_bytes()
    raw = re.sub(rb"(?m)^" + re.escape(HEADER_PREFIX) + rb".*\n", b"", raw)
    return clean(raw)


def cmd_show(a):
    for path in resolve(a.name):
        e = read_entry(path)
        text = text_of(path)
        if a.tail:
            text = "\n".join(text.splitlines()[-a.tail:]) + "\n"
        print(f"=== {e['session']}  pane {e['pane']}  started {fmt_ts(e['created'])}  "
              f"cwd {e['cwd']}  command: {e['command'] or '(shell)'}")
        sys.stdout.write(text)


def cmd_status(a):
    es = entries()
    raw = sum(e["bytes"] for e in es if not e["compacted"])
    comp = sum(e["bytes"] for e in es if e["compacted"])
    ended_raw = sum(e["bytes"] for e in es if not e["compacted"] and not e["active"])
    info = {
        "archive": str(archive_dir()),
        "entries": len(es),
        "active": sum(e["active"] for e in es),
        "raw_bytes": raw,
        "compacted_bytes": comp,
        "compactable_bytes": ended_raw,
        "threshold_bytes": a.threshold * 1024 * 1024,
        "compaction_recommended": ended_raw > a.threshold * 1024 * 1024,
    }
    if a.json:
        print(json.dumps(info, indent=2))
        return
    print(f"archive:     {info['archive']}")
    print(f"entries:     {info['entries']} ({info['active']} active)")
    print(f"raw logs:    {human(raw)} (compactable: {human(ended_raw)})")
    print(f"compacted:   {human(comp)}")
    if info["compaction_recommended"]:
        print(f"RECOMMENDATION: compactable logs exceed {a.threshold} MB, "
              f"run 'tmux-monitor-archive compact'")


def cmd_compact(a):
    cutoff = time.time() - a.older_than * 86400
    saved = 0
    for e in entries():
        if e["compacted"] or e["active"] or e["modified"] > cutoff:
            continue
        path = Path(e["path"])
        target = path.with_suffix(".txt.gz")
        if a.dry_run:
            print(f"would compact {path.name} ({human(e['bytes'])})")
            continue
        header = HEADER_PREFIX.decode() + json.dumps(
            {k: e[k] for k in ("session", "created", "pane", "cwd", "command")},
            ensure_ascii=False) + "\n"
        with gzip.open(target, "wt", encoding="utf-8") as f:
            f.write(header + text_of(path))
        os.chmod(target, 0o600)
        saved += e["bytes"] - target.stat().st_size
        path.unlink()
        print(f"compacted {path.name} -> {target.name}")
    if not a.dry_run:
        print(f"saved {human(max(saved, 0))}")


def cmd_hook_conf(a):
    exe = shlex.quote(str(Path(a.exe or sys.argv[0]).resolve()))
    pipe = (f'pipe-pane -o "exec {exe} log #{{q:session_name}} #{{session_created}} '
            f'#{{pane_id}} #{{q:pane_current_path}} #{{q:pane_start_command}}"')
    print("# tmux-monitor: archive the output of every pane")
    for hook in ("after-new-session", "after-new-window", "after-split-window"):
        print(f"set-hook -g {hook}[42] '{pipe}'")


def main():
    p = argparse.ArgumentParser(description="tmux session archive for tmux-monitor")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("log", help="(internal) called by tmux pipe-pane")
    for arg in ("session", "created", "pane", "cwd", "command"):
        s.add_argument(arg)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("list", help="list archived panes")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="print the cleaned text of a log")
    s.add_argument("name", help="file name, path or session name")
    s.add_argument("--tail", type=int, metavar="N", help="only the last N lines")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("status", help="archive size and compaction advice")
    s.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD_MB, metavar="MB")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("compact", help="clean + gzip logs of ended sessions")
    s.add_argument("--older-than", type=float, default=0, metavar="DAYS")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_compact)

    s = sub.add_parser("hook-conf", help="print the tmux.conf snippet")
    s.add_argument("--exe", help="path of this program (default: as called)")
    s.set_defaults(func=cmd_hook_conf)

    s = sub.add_parser("dir", help="print the archive directory")
    s.set_defaults(func=lambda a: print(archive_dir()))

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
