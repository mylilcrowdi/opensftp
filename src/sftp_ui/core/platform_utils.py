"""
platform_utils — cross-platform helpers.

Centralises all platform-specific logic so the rest of the codebase
stays portable:

* config_dir()          — OS-appropriate config directory
* open_in_file_manager()— reveal a path in Finder / Explorer / Nautilus
* open_ssh_terminal()   — spawn an OS terminal with an SSH session
* PLATFORM              — "darwin" | "win32" | "linux"
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


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


def open_with_editor(path: str) -> None:
    """Open *path* in the OS default editor / associated application.

    * macOS   → ``open <path>``
    * Windows → ``os.startfile(path)``
    * Linux   → ``xdg-open <path>``
    """
    if PLATFORM == "darwin":
        subprocess.Popen(["open", path])
    elif PLATFORM == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


def open_ssh_terminal(
    host: str,
    user: str,
    port: int = 22,
    remote_path: str = "",
    key_path: Optional[str] = None,
) -> None:
    """Spawn the OS terminal emulator with an SSH session pre-configured.

    After connecting the shell changes directory to *remote_path* (if given)
    using a ``cd`` command injected via the terminal's title/command options.

    Args:
        host:        Remote hostname or IP.
        user:        SSH username.
        port:        SSH port (default 22).
        remote_path: Remote directory to ``cd`` into after login (optional).
        key_path:    Absolute path to a private key file (optional).
    """
    # Build the ssh command args (without the terminal wrapper yet).
    ssh_args: list[str] = ["ssh"]
    if port != 22:
        ssh_args += ["-p", str(port)]
    if key_path:
        ssh_args += ["-i", key_path]
    ssh_args.append(f"{user}@{host}")

    # If a remote path was requested, inject a ``cd`` + interactive shell.
    # We use the standard trick of passing a login command that ``cd``s first,
    # then execs the user's default shell so dotfiles are sourced normally.
    if remote_path:
        cd_cmd = f"cd {_shell_quote(remote_path)} && exec $SHELL -l"
        ssh_args += ["-t", cd_cmd]

    if PLATFORM == "darwin":
        # macOS Terminal.app: open a new window running our ssh command.
        # AppleScript lets us pass the exact command without a tmp file.
        ssh_cli = " ".join(_shell_quote(a) for a in ssh_args)
        osa_script = (
            f'tell application "Terminal"\n'
            f'    activate\n'
            f'    do script "{ssh_cli}"\n'
            f'end tell'
        )
        subprocess.Popen(["osascript", "-e", osa_script])
        return

    if PLATFORM == "win32":
        # Windows Terminal (wt) is preferred; fall back to cmd.exe.
        # PowerShell's Start-Process handles spaces in paths correctly.
        ssh_cli = " ".join(ssh_args)
        try:
            subprocess.Popen(
                ["wt", "--", "cmd", "/K", ssh_cli],
                creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
            )
        except FileNotFoundError:
            subprocess.Popen(
                ["cmd", "/K", ssh_cli],
                creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
            )
        return

    # Linux: try common terminal emulators in order of preference.
    # Each emulator has its own flag for the command to execute.
    _TERMINALS: list[tuple[str, list[str]]] = [
        ("gnome-terminal", ["--"]),
        ("xterm",          ["-e"]),
        ("konsole",        ["-e"]),
        ("xfce4-terminal", ["-e"]),
        ("lxterminal",     ["-e"]),
        ("mate-terminal",  ["-e"]),
        ("tilix",          ["-e"]),
        ("alacritty",      ["-e"]),
        ("kitty",          []),
    ]
    for term, flag in _TERMINALS:
        try:
            subprocess.Popen([term] + flag + ssh_args)
            return
        except FileNotFoundError:
            continue

    raise RuntimeError(
        "No supported terminal emulator found. "
        "Install gnome-terminal, xterm, konsole, or another terminal."
    )


def _shell_quote(s: str) -> str:
    """Minimal shell-safe quoting for building SSH command strings.

    Uses single quotes and escapes embedded single quotes.
    """
    return "'" + s.replace("'", "'\\''") + "'"


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
