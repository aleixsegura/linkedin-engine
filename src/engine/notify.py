"""Notificaciones nativas de macOS."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def notify(title: str, message: str, open_path: Path | None = None) -> None:
    if shutil.which("terminal-notifier"):
        cmd = ["terminal-notifier", "-title", title, "-message", message, "-group", "linkedin-engine"]
        if open_path:
            cmd += ["-open", open_path.resolve().as_uri()]
        subprocess.run(cmd, check=False)
        return
    script = f"display notification {_quote(message)} with title {_quote(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
