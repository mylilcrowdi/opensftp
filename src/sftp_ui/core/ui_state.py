"""
UIState — persists lightweight UI state across sessions.

Stored in: ~/.config/sftp-ui/ui_state.json

Fields:
  last_local_path      — last visited local directory
  last_connection_id   — UUID of the connection last used
  last_remote_paths    — per-connection-ID last visited remote path

Edge-case policy
----------------
Local path:
  - If the saved path is gone, walk up the tree until an existing ancestor
    is found; fall back to the user's home directory.
  - A file (not a dir) at the saved path is treated the same as missing.

Remote path:
  - Stored per connection ID so different servers keep independent history.
  - On reconnect the panel tries to navigate to the saved path; any SFTP
    exception causes a silent fallback to "/".
  - If the server's filesystem layout changed (renamed root, chroot, etc.)
    the user simply lands at "/" — no error dialog.

Last connection:
  - If the saved connection ID was deleted from the store, nothing is
    pre-selected (the combo shows the placeholder text).  The stale ID is
    left in state and will be ignored again next time.

Auto-reconnect:
  - was_connected is set True only when a connection is fully established
    (_on_connect_success), and False on clean disconnect or failed connect.
  - If the process crashes or is killed, closeEvent never runs and
    was_connected stays True — intentional: next startup treats a crash
    the same as "was connected", so the auto-reconnect fires.
  - A single reconnect attempt is made; if it fails, was_connected is
    cleared so subsequent startups do not keep retrying a dead server.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sftp_ui.core.platform_utils import config_dir


DEFAULT_STATE_PATH = config_dir() / "ui_state.json"


class UIState:
    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self._path = Path(path)
        self.last_local_path: str = str(Path.home())
        self.last_connection_id: Optional[str] = None
        self.last_remote_paths: dict[str, str] = {}   # conn_id → remote path
        self.was_connected: bool = False
        self.column_widths: dict[str, list[int]] = {}   # panel_id → [col_width, ...]
        self._load()

    # ── Accessors ────────────────────────────────────────────────────────────

    def local_path(self) -> str:
        """Return the saved local path, falling back to an existing ancestor."""
        p = Path(self.last_local_path)
        # Walk up until we find an existing directory
        while True:
            if p.is_dir():
                return str(p)
            parent = p.parent
            if parent == p:
                # Reached filesystem root and it's still missing — use home
                break
            p = parent
        return str(Path.home())

    def remote_path(self, connection_id: str) -> str:
        """Return the saved remote path for a connection, defaulting to '/'."""
        return self.last_remote_paths.get(connection_id, "/")

    # ── Mutators ─────────────────────────────────────────────────────────────

    def set_local_path(self, path: str) -> None:
        self.last_local_path = path
        self.save()

    def set_remote_path(self, connection_id: str, path: str) -> None:
        self.last_remote_paths[connection_id] = path
        self.save()

    def set_last_connection(self, connection_id: str) -> None:
        self.last_connection_id = connection_id
        self.save()

    def set_was_connected(self, value: bool) -> None:
        self.was_connected = value
        self.save()

    def set_column_widths(self, panel: str, widths: list[int]) -> None:
        self.column_widths[panel] = widths
        self.save()

    def get_column_widths(self, panel: str) -> list[int]:
        return self.column_widths.get(panel, [])

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "last_local_path": self.last_local_path,
                "last_connection_id": self.last_connection_id,
                "last_remote_paths": self.last_remote_paths,
                "was_connected": self.was_connected,
                "column_widths": self.column_widths,
            }
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass  # non-fatal: state simply won't persist this session

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.last_local_path = str(data.get("last_local_path", Path.home()))
            self.last_connection_id = data.get("last_connection_id")
            rp = data.get("last_remote_paths", {})
            if isinstance(rp, dict):
                self.last_remote_paths = {str(k): str(v) for k, v in rp.items()}
            self.was_connected = bool(data.get("was_connected", False))
            cw = data.get("column_widths", {})
            if isinstance(cw, dict):
                safe: dict[str, list[int]] = {}
                for k, v in cw.items():
                    if isinstance(v, list):
                        try:
                            safe[str(k)] = [int(x) for x in v]
                        except (TypeError, ValueError):
                            pass  # skip rows with non-numeric entries
                self.column_widths = safe
        except (json.JSONDecodeError, OSError, AttributeError):
            pass  # corrupt file — start from defaults
