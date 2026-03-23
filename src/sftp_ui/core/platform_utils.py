"""
platform_utils — cross-platform helpers.

Centralises all platform-specific logic so the rest of the codebase
stays portable:

* config_dir()          — OS-appropriate config directory
* open_in_file_manager()— reveal a path in Finder / Explorer / Nautilus
* PLATFORM              — "darwin" | "win32" | "linux"
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# One-stop constant so callers do ``from sftp_ui.core.platform_utils import PLATFORM``.
PLATFORM: str = sys.platform  # "darwin" | "win32" | "linux"


def config_dir() -> Path:
    """
    Return the platform-appropriate config directory for sftp-ui.

    * macOS / Linux  → ``$XDG_CONFIG_HOME/sftp-ui`` or ``~/.config/sftp-ui``
    * Windows        → ``%APPDATA%\\sftp-ui``  (e.g. C:\\Users\\name\\AppData\\Roaming\\sftp-ui)

    The directory is *not* created here; callers are responsible for that.
    """
    if PLATFORM == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "sftp-ui"

    # XDG base-dir spec (Linux); macOS follows the same convention for simplicity
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "sftp-ui"
    return Path.home() / ".config" / "sftp-ui"


def open_in_file_manager(path: str) -> None:
    """
    Reveal *path* in the native file manager.

    * macOS   → ``open -R <path>``  (reveals in Finder)
    * Windows → ``explorer /select,<path>``  (reveals in Explorer)
    * Linux   → tries xdg-open on the parent directory; falls back to
                nautilus / dolphin / thunar / nemo if xdg-open is unavailable.
    """
    p = Path(path)

    if PLATFORM == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
        return

    if PLATFORM == "win32":
        # /select highlights the item; works for both files and directories
        subprocess.Popen(["explorer", f"/select,{p}"])
        return

    # Linux — use the directory so any file manager can open it
    target = str(p) if p.is_dir() else str(p.parent)
    try:
        subprocess.Popen(["xdg-open", target])
        return
    except FileNotFoundError:
        pass

    # Fallback: try common GTK/KDE/Xfce file managers
    for fm in ("nautilus", "dolphin", "thunar", "nemo", "pcmanfm"):
        try:
            subprocess.Popen([fm, target])
            return
        except FileNotFoundError:
            continue


def file_manager_action_label(is_dir: bool = False) -> str:  # noqa: ARG001
    """
    Return a human-readable "Open in …" label for the context menu,
    matching the native file manager name on each platform.
    """
    if PLATFORM == "darwin":
        return "↗  Open in Finder"
    if PLATFORM == "win32":
        return "↗  Open in Explorer"
    return "↗  Open in File Manager"
