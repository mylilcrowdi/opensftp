"""
RemoteSearch — recursive file search on the remote server.

Walks the remote directory tree via SFTP (or exec_command("find ...") if
available) and streams matches back to the UI via Qt signals.
"""
from __future__ import annotations

import fnmatch
import re
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient


class _SearchSignals(QObject):
    match_found = Signal(object)       # RemoteEntry
    search_done = Signal(int)          # total dirs scanned
    search_error = Signal(str)         # error message


class RemoteSearch:
    """Recursive remote file search with cancellation and streaming results.

    Usage::

        search = RemoteSearch(sftp_client)
        search.signals.match_found.connect(on_match)
        search.signals.search_done.connect(on_done)
        search.start("/home/user", "*.py", use_regex=False, max_depth=5)
        # ...
        search.cancel()  # if the user wants to stop early
    """

    def __init__(self, sftp: SFTPClient) -> None:
        self._sftp = sftp
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.signals = _SearchSignals()

    def start(
        self,
        root: str,
        pattern: str,
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_depth: int = 5,
    ) -> None:
        """Start searching in a background thread."""
        self._cancel_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(root, pattern, use_regex, case_sensitive, max_depth),
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        """Signal the search to stop at the next convenient point."""
        self._cancel_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internals ─────────────────────────────────────────────────────────────

    def _run(
        self,
        root: str,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        max_depth: int,
    ) -> None:
        matcher = self._build_matcher(pattern, use_regex, case_sensitive)
        dirs_scanned = 0

        # Try exec_command("find ...") first — much faster on capable servers
        if not use_regex and self._try_exec_find(root, pattern, case_sensitive, max_depth, matcher):
            return

        # Fallback: walk via SFTP listdir
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            if self._cancel_event.is_set():
                break
            path, depth = stack.pop()
            if depth > max_depth:
                continue

            try:
                entries = self._sftp.listdir(path)
            except Exception:
                continue  # permission denied or broken symlink

            dirs_scanned += 1
            for entry in entries:
                if self._cancel_event.is_set():
                    break
                if entry.name in (".", ".."):
                    continue
                if matcher(entry.name):
                    self.signals.match_found.emit(entry)
                if entry.is_dir and depth < max_depth:
                    stack.append((entry.path, depth + 1))

        self.signals.search_done.emit(dirs_scanned)

    def _try_exec_find(
        self,
        root: str,
        pattern: str,
        case_sensitive: bool,
        max_depth: int,
        matcher: Callable[[str], bool],
    ) -> bool:
        """Try to use ssh exec_command('find ...') for fast search.

        Returns True if exec_command succeeded (results were emitted).
        Returns False if the server doesn't support exec_command.
        """
        try:
            ssh = self._sftp._ssh
            if ssh is None:
                return False

            name_flag = "-name" if case_sensitive else "-iname"
            cmd = f"find {_shell_quote(root)} -maxdepth {max_depth} {name_flag} {_shell_quote(pattern)} -print 2>/dev/null"
            _, stdout, _ = ssh.exec_command(cmd, timeout=30)
            output = stdout.read().decode("utf-8", errors="replace")

            if not output.strip():
                self.signals.search_done.emit(0)
                return True

            dirs_scanned = 0
            for line in output.strip().split("\n"):
                if self._cancel_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                name = line.rsplit("/", 1)[-1]
                # Re-check with our matcher since find's glob may differ slightly
                if matcher(name):
                    entry = RemoteEntry(
                        name=name,
                        path=line,
                        is_dir=False,  # find output doesn't tell us; assume file
                        size=0,
                        mtime=0,
                    )
                    self.signals.match_found.emit(entry)
                    dirs_scanned += 1

            self.signals.search_done.emit(dirs_scanned)
            return True

        except Exception:
            # Server may not allow exec_command (chroot SFTP-only, etc.)
            return False

    @staticmethod
    def _build_matcher(
        pattern: str, use_regex: bool, case_sensitive: bool
    ) -> Callable[[str], bool]:
        if use_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                rx = re.compile(pattern, flags)
            except re.error:
                return lambda _name: False
            return lambda name: rx.search(name) is not None
        else:
            if case_sensitive:
                return lambda name: fnmatch.fnmatch(name, pattern)
            else:
                pat_lower = pattern.lower()
                return lambda name: fnmatch.fnmatch(name.lower(), pat_lower)


def _shell_quote(s: str) -> str:
    """Minimal shell quoting — wrap in single quotes, escape embedded ones."""
    return "'" + s.replace("'", "'\\''") + "'"
