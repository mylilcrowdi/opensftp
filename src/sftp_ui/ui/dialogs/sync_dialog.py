"""
SyncDialog — compare a local directory with a remote directory and transfer
selected files in either direction.

Layout:
  ┌────────────────────────────────────────────────────────────────────────────┐
  │  /Users/async/project  ↔  /var/www/html/project            [↻ Rescan]     │
  ├────────────────────────────────────────────────────────────────────────────┤
  │  [↑ Local only ·2]  [↑ Local newer ·1]  [✓ Same ·14]  [↓ Remote only ·3] │
  ├──┬──────────────────┬───────────────────────────────┬──────────┬──────────┤
  │  │ Status           │ Path                          │ Local    │ Remote   │
  │☑ │ ↑  Local only    │ src/new_feature.py            │ 2.1 KB   │ —        │
  │☑ │ ↑  Local newer   │ src/main.py                   │ 8.4 KB   │ 7.9 KB   │
  │☐ │ ✓  Same          │ src/utils.py                  │ 1.2 KB   │ 1.2 KB   │
  │☐ │ ↓  Remote only   │ src/old.py                    │ —        │ 3.4 KB   │
  ├──┴──────────────────┴───────────────────────────────┴──────────┴──────────┤
  │  18 files · 2 local only · 1 local newer · 14 same · 3 remote only        │
  │        [Select all] [Select none]  [↑ Upload selected (3)] [↓ Download…]  │
  └────────────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
import queue
import threading
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableView, QVBoxLayout,
)

from sftp_ui.core.connection import Connection
from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient
from sftp_ui.core.transfer import TransferDirection, TransferJob


# ── Status enum ───────────────────────────────────────────────────────────────

class SyncStatus(Enum):
    LOCAL_ONLY   = "Local only"
    LOCAL_NEWER  = "Local newer"
    SAME         = "Same"
    REMOTE_NEWER = "Remote newer"
    REMOTE_ONLY  = "Remote only"


_ICON = {
    SyncStatus.LOCAL_ONLY:   "↑",
    SyncStatus.LOCAL_NEWER:  "↑",
    SyncStatus.SAME:         "✓",
    SyncStatus.REMOTE_NEWER: "↓",
    SyncStatus.REMOTE_ONLY:  "↓",
}
_COLOR = {
    SyncStatus.LOCAL_ONLY:   "#89b4fa",
    SyncStatus.LOCAL_NEWER:  "#fab387",
    SyncStatus.SAME:         "#585b70",
    SyncStatus.REMOTE_NEWER: "#f9e2af",
    SyncStatus.REMOTE_ONLY:  "#a6e3a1",
}
# Default checked state — local-side changes on, remote-side and same off
_DEFAULT_CHECKED = {
    SyncStatus.LOCAL_ONLY:   True,
    SyncStatus.LOCAL_NEWER:  True,
    SyncStatus.SAME:         False,
    SyncStatus.REMOTE_NEWER: False,
    SyncStatus.REMOTE_ONLY:  False,
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SyncEntry:
    rel_path:     str
    status:       SyncStatus
    local_abs:    Optional[str] = None
    remote_abs:   Optional[str] = None
    local_size:   int   = 0
    remote_size:  int   = 0
    local_mtime:  float = 0.0
    remote_mtime: float = 0.0
    checked:      bool  = True


def _human_size(n: int) -> str:
    if n == 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_mtime(ts: float) -> str:
    if ts == 0.0:
        return "—"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ── Table model ───────────────────────────────────────────────────────────────

_COLS = ("", "Status", "Path", "Local", "Remote", "Local modified", "Remote modified")
_C_CHK, _C_ST, _C_PATH, _C_LS, _C_RS, _C_LM, _C_RM = range(7)


class _SyncModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[SyncEntry] = []

    def load(self, rows: list[SyncEntry]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, _=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, _=QModelIndex()) -> int:
        return len(_COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLS[section]
        return None

    def flags(self, index: QModelIndex):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == _C_CHK:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        e = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == _C_CHK:
            return Qt.CheckState.Checked if e.checked else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DisplayRole:
            if col == _C_ST:   return f"{_ICON[e.status]}  {e.status.value}"
            if col == _C_PATH: return e.rel_path
            if col == _C_LS:   return _human_size(e.local_size)
            if col == _C_RS:   return _human_size(e.remote_size)
            if col == _C_LM:   return _fmt_mtime(e.local_mtime)
            if col == _C_RM:   return _fmt_mtime(e.remote_mtime)

        if role == Qt.ItemDataRole.ForegroundRole and col == _C_ST:
            return QColor(_COLOR[e.status])

        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == _C_CHK:
            self._rows[index.row()].checked = (value == Qt.CheckState.Checked or value == 2)
            self.dataChanged.emit(index, index, [role])
            return True
        return False


# ── Scan signals ──────────────────────────────────────────────────────────────

class _ScanSignals(QObject):
    progress = Signal(str)
    done     = Signal(list)   # list[SyncEntry]
    error    = Signal(str)


# ── Parallel remote walk ──────────────────────────────────────────────────────

_WALK_WORKERS = 3   # simultaneous SFTP connections for directory listing


def _walk_remote_parallel(
    conn: Connection,
    remote_dir: str,
    progress: Callable[[str], None],
    cancel: threading.Event,
) -> list[RemoteEntry]:
    """BFS directory walk using up to _WALK_WORKERS parallel SFTP connections.

    Each worker owns one SFTPClient exclusively — no sharing, no locking on
    the network layer.  A shared work queue distributes subdirectories; a
    pending counter tracks when all work is done.

    If fewer than _WALK_WORKERS connections succeed (server limit etc.) the
    scan continues with however many opened successfully.
    """
    results: list[RemoteEntry] = []
    lock = threading.Lock()
    work_q: queue.Queue[Optional[str]] = queue.Queue()
    work_q.put(remote_dir)
    pending = [1]          # dirs queued + in-flight; protected by lock
    all_done = threading.Event()
    dirs_seen = [0]
    visited: set[str] = {remote_dir}   # guard against symlink loops

    # Open dedicated connections — fewer if server refuses extras.
    clients: list[SFTPClient] = []
    for _ in range(_WALK_WORKERS):
        try:
            c = SFTPClient()
            c.connect(conn)
            clients.append(c)
        except Exception:
            break

    if not clients:
        raise ConnectionError("Could not open any connection for the scan")

    def worker(client: SFTPClient) -> None:
        while not cancel.is_set():
            try:
                path = work_q.get(timeout=0.2)
            except queue.Empty:
                with lock:
                    if pending[0] == 0:
                        break
                continue

            if path is None:    # poison pill — time to stop
                break

            subdirs: list[str] = []
            try:
                entries = client.listdir(path)
                with lock:
                    for e in entries:
                        if e.is_dir:
                            # Skip symlinked directories to prevent infinite
                            # recursion when a symlink points to an ancestor.
                            if e.is_symlink:
                                continue
                            if e.path not in visited:
                                visited.add(e.path)
                                subdirs.append(e.path)
                        else:
                            results.append(e)
                    dirs_seen[0] += 1
                progress(f"Scanning remote… {dirs_seen[0]} director{'y' if dirs_seen[0] == 1 else 'ies'}")
            except Exception:
                pass    # skip unreadable dirs, keep going

            with lock:
                for d in subdirs:
                    pending[0] += 1
                    work_q.put(d)
                pending[0] -= 1
                if pending[0] == 0:
                    all_done.set()

    threads = [
        threading.Thread(target=worker, args=(c,), daemon=True)
        for c in clients
    ]
    for t in threads:
        t.start()

    # Block until finished or cancelled.
    while not all_done.is_set():
        if cancel.is_set():
            break
        all_done.wait(timeout=0.1)

    # Stop all workers.
    for _ in threads:
        work_q.put(None)
    for t in threads:
        t.join(timeout=5.0)

    for c in clients:
        try:
            c.close()
        except Exception:
            pass

    return results


# ── Scan logic ────────────────────────────────────────────────────────────────

def _scan(
    local_dir: str,
    remote_dir: str,
    conn: Connection,
    progress: Callable[[str], None],
    cancel: threading.Event,
) -> list[SyncEntry]:
    progress("Scanning local files…")
    local: dict[str, tuple[str, int, float]] = {}
    local_root = Path(local_dir)
    for p in sorted(local_root.rglob("*")):
        if cancel.is_set():
            return []
        if p.is_file():
            st = p.stat()
            local[p.relative_to(local_root).as_posix()] = (str(p), st.st_size, st.st_mtime)

    remote_entries = _walk_remote_parallel(conn, remote_dir, progress, cancel)
    if cancel.is_set():
        return []

    progress("Comparing…")
    remote: dict[str, tuple[str, int, float]] = {}
    remote_root = PurePosixPath(remote_dir)
    for e in remote_entries:
        rel = str(PurePosixPath(e.path).relative_to(remote_root))
        remote[rel] = (e.path, e.size, float(e.mtime))

    entries: list[SyncEntry] = []
    for rel in sorted(set(local) | set(remote)):
        in_l = rel in local
        in_r = rel in remote

        if in_l and not in_r:
            la, ls, lm = local[rel]
            st = SyncStatus.LOCAL_ONLY
            entries.append(SyncEntry(rel, st, local_abs=la, local_size=ls,
                                     local_mtime=lm, checked=_DEFAULT_CHECKED[st]))
        elif in_r and not in_l:
            ra, rs, rm = remote[rel]
            st = SyncStatus.REMOTE_ONLY
            entries.append(SyncEntry(rel, st, remote_abs=ra, remote_size=rs,
                                     remote_mtime=rm, checked=_DEFAULT_CHECKED[st]))
        else:
            la, ls, lm = local[rel]
            ra, rs, rm = remote[rel]
            if ls == rs:
                st = SyncStatus.SAME
            elif abs(lm - rm) <= 2.0:
                # Treat mtimes within ±2 s as equal — FAT/exFAT filesystems
                # store timestamps with 2-second granularity so a strict
                # comparison would falsely flag every FAT-hosted file as
                # modified after the first round-trip.
                st = SyncStatus.SAME
            elif lm > rm:
                st = SyncStatus.LOCAL_NEWER
            else:
                st = SyncStatus.REMOTE_NEWER
            entries.append(SyncEntry(rel, st, local_abs=la, remote_abs=ra,
                                     local_size=ls, remote_size=rs,
                                     local_mtime=lm, remote_mtime=rm,
                                     checked=_DEFAULT_CHECKED[st]))
    return entries


# ── Job builders (pure functions — no Qt required) ────────────────────────────

def _build_upload_jobs(
    entries: list[SyncEntry],
    remote_dir: str,
) -> list[TransferJob]:
    """Return upload TransferJobs for every checked entry that has a local path."""
    jobs: list[TransferJob] = []
    for e in entries:
        if not e.checked or e.local_abs is None:
            continue
        remote = e.remote_abs or str(PurePosixPath(remote_dir) / e.rel_path)
        job = TransferJob(
            local_path=e.local_abs,
            remote_path=remote,
            direction=TransferDirection.UPLOAD,
        )
        job.total_bytes = e.local_size
        jobs.append(job)
    return jobs


def _build_download_jobs(
    entries: list[SyncEntry],
    local_dir: str,
) -> list[TransferJob]:
    """Return download TransferJobs for every checked entry that has a remote path.

    Parent directories are created on-demand so the download engine can write
    the file immediately after the job is enqueued.
    """
    jobs: list[TransferJob] = []
    for e in entries:
        if not e.checked or e.remote_abs is None:
            continue
        local = e.local_abs or str(Path(local_dir) / e.rel_path)
        os.makedirs(os.path.dirname(os.path.abspath(local)), exist_ok=True)
        job = TransferJob(
            local_path=local,
            remote_path=e.remote_abs,
            direction=TransferDirection.DOWNLOAD,
        )
        job.total_bytes = e.remote_size
        jobs.append(job)
    return jobs


# ── Dialog ────────────────────────────────────────────────────────────────────

class SyncDialog(QDialog):
    def __init__(
        self,
        local_dir: str,
        remote_dir: str,
        conn: Connection,
        on_enqueue: Callable[[list[TransferJob]], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync Preview")
        self.resize(960, 560)

        self._local_dir  = local_dir
        self._remote_dir = remote_dir
        self._conn       = conn
        self._on_enqueue = on_enqueue

        self._all_entries: list[SyncEntry] = []
        self._visible: set[SyncStatus] = set(SyncStatus) - {SyncStatus.SAME}
        self._scan_cancel = threading.Event()

        self._signals = _ScanSignals()
        self._signals.progress.connect(self._on_scan_progress)
        self._signals.done.connect(self._on_scan_done)
        self._signals.error.connect(self._on_scan_error)

        self._model = _SyncModel()
        self._build_ui()
        self._start_scan()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # Header
        header_row = QHBoxLayout()
        self._header_label = QLabel(
            f"<b>{self._local_dir}</b>  ↔  <b>{self._remote_dir}</b>"
        )
        self._rescan_btn = QPushButton("↻  Rescan")
        self._rescan_btn.setFixedWidth(90)
        self._rescan_btn.clicked.connect(self._start_scan)
        header_row.addWidget(self._header_label, stretch=1)
        header_row.addWidget(self._rescan_btn)
        root.addLayout(header_row)

        # Filter toggles — one per status, show counts after scan
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        self._filter_btns: dict[SyncStatus, QPushButton] = {}
        for st in SyncStatus:
            btn = QPushButton(f"{_ICON[st]}  {st.value}")
            btn.setCheckable(True)
            btn.setChecked(st in self._visible)
            btn.toggled.connect(lambda checked, s=st: self._on_filter_toggle(s, checked))
            self._filter_btns[st] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        # Table
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_C_CHK, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_C_CHK, 28)
        for col in (_C_ST, _C_PATH, _C_LS, _C_RS, _C_LM, _C_RM):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        # Sensible default widths — user can drag to resize any column.
        self._table.setColumnWidth(_C_ST,   140)
        self._table.setColumnWidth(_C_PATH, 320)
        self._table.setColumnWidth(_C_LS,    72)
        self._table.setColumnWidth(_C_RS,    72)
        self._table.setColumnWidth(_C_LM,   130)
        self._table.setColumnWidth(_C_RM,   130)
        root.addWidget(self._table, stretch=1)

        # Summary
        self._summary_label = QLabel("Scanning…")
        self._summary_label.setStyleSheet("color: #7f849c; font-size: 11px;")
        root.addWidget(self._summary_label)

        # Action row
        action_row = QHBoxLayout()
        self._select_all_btn = QPushButton("Select all")
        self._select_all_btn.setFixedWidth(90)
        self._select_all_btn.clicked.connect(self._select_all)
        self._select_none_btn = QPushButton("Select none")
        self._select_none_btn.setFixedWidth(90)
        self._select_none_btn.clicked.connect(self._select_none)

        self._upload_btn = QPushButton("↑  Upload selected")
        self._upload_btn.setObjectName("primary")
        self._upload_btn.setEnabled(False)
        self._upload_btn.clicked.connect(self._do_upload)

        self._download_btn = QPushButton("↓  Download selected")
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self._do_download)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._close)

        action_row.addWidget(self._select_all_btn)
        action_row.addWidget(self._select_none_btn)
        action_row.addStretch()
        action_row.addWidget(self._upload_btn)
        action_row.addWidget(self._download_btn)
        action_row.addWidget(close_btn)
        root.addLayout(action_row)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _start_scan(self) -> None:
        # Cancel any scan already in flight before starting a new one.
        self._scan_cancel.set()
        self._scan_cancel = threading.Event()
        cancel = self._scan_cancel   # local ref for the closure

        self._rescan_btn.setEnabled(False)
        self._upload_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._summary_label.setText("Scanning…")
        self._model.load([])
        self._all_entries = []

        def _run() -> None:
            try:
                entries = _scan(
                    self._local_dir, self._remote_dir, self._conn,
                    progress=self._signals.progress.emit,
                    cancel=cancel,
                )
                if not cancel.is_set():
                    self._signals.done.emit(entries)
            except Exception as exc:
                if not cancel.is_set():
                    self._signals.error.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_scan_progress(self, msg: str) -> None:
        self._summary_label.setText(msg)

    def _on_scan_done(self, entries: list[SyncEntry]) -> None:
        self._all_entries = entries
        self._update_filter_btn_labels()
        self._apply_filters()
        self._rescan_btn.setEnabled(True)
        self._upload_btn.setEnabled(True)
        self._download_btn.setEnabled(True)
        self._update_summary()

    def _on_scan_error(self, msg: str) -> None:
        self._summary_label.setText(f"Scan error: {msg}")
        self._rescan_btn.setEnabled(True)

    # ── Filters ───────────────────────────────────────────────────────────────

    def _on_filter_toggle(self, status: SyncStatus, checked: bool) -> None:
        if checked:
            self._visible.add(status)
        else:
            self._visible.discard(status)
        self._apply_filters()

    def _apply_filters(self) -> None:
        self._model.load([e for e in self._all_entries if e.status in self._visible])

    def _update_filter_btn_labels(self) -> None:
        counts = Counter(e.status for e in self._all_entries)
        for st, btn in self._filter_btns.items():
            btn.setText(f"{_ICON[st]}  {st.value} · {counts.get(st, 0)}")

    def _update_summary(self) -> None:
        counts = Counter(e.status for e in self._all_entries)
        parts = [f"{counts[st]} {st.value.lower()}"
                 for st in SyncStatus if counts.get(st)]
        self._summary_label.setText(
            f"{len(self._all_entries)} file(s) · " + " · ".join(parts)
        )

    # ── Select all / none ─────────────────────────────────────────────────────

    def _set_all_checked(self, checked: bool) -> None:
        for e in self._model._rows:
            e.checked = checked
        if self._model.rowCount() > 0:
            self._model.dataChanged.emit(
                self._model.index(0, _C_CHK),
                self._model.index(self._model.rowCount() - 1, _C_CHK),
                [Qt.ItemDataRole.CheckStateRole],
            )

    def _close(self) -> None:
        self._scan_cancel.set()
        self.reject()

    def closeEvent(self, event) -> None:
        self._scan_cancel.set()
        super().closeEvent(event)

    def _select_all(self) -> None:
        self._set_all_checked(True)

    def _select_none(self) -> None:
        self._set_all_checked(False)

    # ── Transfer actions ──────────────────────────────────────────────────────

    def _do_upload(self) -> None:
        jobs = _build_upload_jobs(self._model._rows, self._remote_dir)
        if not jobs:
            self._summary_label.setText("No checked files with a local path to upload.")
            return
        self._on_enqueue(jobs)
        self.accept()

    def _do_download(self) -> None:
        jobs = _build_download_jobs(self._model._rows, self._local_dir)
        if not jobs:
            self._summary_label.setText("No checked files with a remote path to download.")
            return
        self._on_enqueue(jobs)
        self.accept()
