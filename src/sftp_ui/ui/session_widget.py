"""
SessionWidget — encapsulates a single SFTP connection session.

Each tab in the MainWindow holds one SessionWidget that owns:
- LocalPanel + RemotePanel in a splitter
- TransferPanel for this session's transfers
- SFTPClient, TransferQueue, and connection state
- Auto-reconnect health timer
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QMessageBox, QSplitter, QVBoxLayout, QWidget,
)

from sftp_ui.core.connection import Connection
from sftp_ui.core.platform_utils import open_ssh_terminal
from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient
from sftp_ui.core.transfer import (
    TransferDirection, TransferEngine, TransferJob, TransferState,
)
from sftp_ui.core.queue import TransferQueue
from sftp_ui.ui.glass_frame import GlassFrame
from sftp_ui.ui.panels.local_panel import LocalPanel
from sftp_ui.ui.panels.remote_panel import RemotePanel
from sftp_ui.ui.widgets.transfer_panel import TransferPanel

if TYPE_CHECKING:
    from sftp_ui.core.ui_state import UIState

_OVERWRITE_OVERWRITE = "overwrite"
_OVERWRITE_SKIP      = "skip"
_OVERWRITE_CANCEL    = "cancel"


class _SessionSignals(QObject):
    """Thread-safe signal bridge for one session."""
    status           = Signal(str)
    job_enqueued     = Signal(object)
    job_started      = Signal(object)
    job_progress     = Signal(object, int, int)
    job_done         = Signal(object)
    job_failed       = Signal(object)
    job_cancelled    = Signal(object)
    refresh_remote   = Signal()
    navigate_remote  = Signal(str)
    connect_success  = Signal()
    connect_failed   = Signal(str)
    set_sftp         = Signal(object)
    reconnecting     = Signal()
    reconnected      = Signal()
    reconnect_failed = Signal(str)
    show_overwrite_dialog = Signal(list)
    cross_upload     = Signal(list, str)  # (local_paths, remote_dir) — from bg thread


class SessionWidget(QWidget):
    """One SFTP session — owns its own connection, panels, and queue."""

    # Emitted so MainWindow can update the tab title / status dot
    connection_changed = Signal(object)   # Connection or None
    status_message     = Signal(str)
    job_finished       = Signal(object)   # TransferJob — for history recording
    reconnect_state_changed = Signal()    # Emitted when reconnecting starts/ends
    cross_session_transfer = Signal(str, list, str)  # (source_session_id, entry_dicts, dest_dir)

    def __init__(
        self,
        ui_state: Optional["UIState"] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ui_state = ui_state
        self._sftp: Optional[SFTPClient] = None
        self._queue: Optional[TransferQueue] = None
        self._active_conn: Optional[Connection] = None
        self._auto_reconnect = False
        self._reconnecting = False
        self._signals = _SessionSignals()

        # Overwrite-conflict resolution
        self._overwrite_event: threading.Event = threading.Event()
        self._overwrite_result: str = _OVERWRITE_CANCEL

        self._build_ui()
        self._connect_signals()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(8, 8, 8, 4)
        cl.setSpacing(4)

        local_path = None
        if self._ui_state:
            local_path = self._ui_state.local_path()

        splitter = QSplitter()
        splitter.setHandleWidth(4)

        # Local panel in glass frame
        self._glass_local = GlassFrame()
        self.local_panel = LocalPanel(initial_path=local_path)
        self._glass_local.layout().addWidget(self.local_panel)
        splitter.addWidget(self._glass_local)

        # Remote panel in glass frame
        self._glass_remote = GlassFrame()
        self.remote_panel = RemotePanel()
        self._glass_remote.layout().addWidget(self.remote_panel)
        splitter.addWidget(self._glass_remote)

        splitter.setSizes([380, 820])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self._glass_local.setMinimumWidth(200)
        self._glass_remote.setMinimumWidth(200)
        cl.addWidget(splitter, stretch=1)

        # Transfer panel in glass frame
        self._glass_transfer = GlassFrame()
        self.transfer_panel = TransferPanel()
        self.transfer_panel.cancel_requested.connect(self._on_cancel)
        self.transfer_panel.resume_requested.connect(self._on_resume)
        self.transfer_panel.pause_resume_requested.connect(self._on_pause_resume)
        self._glass_transfer.layout().addWidget(self.transfer_panel)
        cl.addWidget(self._glass_transfer)

        layout.addWidget(content)

        # Glass frames list for frost theme
        self._glass_frames = [
            self._glass_local, self._glass_remote, self._glass_transfer,
        ]

        # Timers
        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(300)
        self._refresh_debounce.timeout.connect(self.remote_panel.refresh)

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(10_000)
        self._health_timer.timeout.connect(self._check_connection_health)

    def _connect_signals(self) -> None:
        sig = self._signals
        sig.job_enqueued.connect(self.transfer_panel.add_job)
        sig.job_started.connect(self.transfer_panel.refresh_job)
        sig.job_progress.connect(self.transfer_panel.update_progress)
        sig.job_done.connect(self._on_job_done)
        sig.job_failed.connect(self._on_job_failed)
        sig.job_cancelled.connect(self._on_job_cancelled)
        sig.refresh_remote.connect(self.remote_panel.refresh)
        sig.navigate_remote.connect(self.remote_panel.navigate_or_root)
        sig.set_sftp.connect(self.remote_panel.set_sftp)
        sig.status.connect(self.status_message)
        sig.reconnecting.connect(self._on_reconnecting)
        sig.reconnected.connect(self._on_reconnected)
        sig.reconnect_failed.connect(self._on_reconnect_failed)
        sig.show_overwrite_dialog.connect(self._on_show_overwrite_dialog)
        sig.cross_upload.connect(self._on_upload_requested)
        sig.connect_success.connect(self.on_connect_success)
        sig.connect_failed.connect(self._on_connect_failed)

        self.remote_panel.upload_requested.connect(self._on_upload_requested)
        self.remote_panel.download_requested.connect(self._on_download_requested)
        self.remote_panel.remote_copy_requested.connect(self._on_remote_copy_requested)
        self.remote_panel.cross_session_drop.connect(self._on_cross_session_drop)
        self.remote_panel.open_terminal_requested.connect(self._on_open_terminal_requested)
        self.local_panel.download_drop_requested.connect(self._on_download_drop)
        self.remote_panel.status_message.connect(self.status_message)
        self.local_panel.status_message.connect(self.status_message)

    # ── Glass / Frost ─────────────────────────────────────────────────────────

    def set_frost_active(self, active: bool) -> None:
        for frame in self._glass_frames:
            frame.set_frost_active(active)

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def active_conn(self) -> Optional[Connection]:
        return self._active_conn

    @property
    def is_connected(self) -> bool:
        return self._sftp is not None and self._sftp.is_connected()

    def connect_to(self, conn: Connection) -> None:
        """Start a connection in a background thread."""
        self._active_conn = conn

        saved_remote = "/"
        if self._ui_state:
            saved_remote = self._ui_state.remote_path(conn.id)

        def _do_connect():
            sftp = SFTPClient()
            try:
                sftp.connect(conn)
            except Exception as exc:
                self._signals.connect_failed.emit(str(exc))
                return

            self._sftp = sftp
            self._setup_queue(conn)
            self._signals.set_sftp.emit(sftp)
            self._signals.connect_success.emit()
            self._signals.status.emit(f"Connected to {conn.host}")
            self._signals.navigate_remote.emit(saved_remote)

        threading.Thread(target=_do_connect, daemon=True).start()

    def disconnect(self) -> bool:
        """Disconnect from server. Returns False if user cancelled."""
        if self._queue and self._queue.pending_count() > 0:
            n = self._queue.pending_count()
            reply = QMessageBox.question(
                self, "Transfers in progress",
                f"{n} transfer(s) still in progress. Cancel them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        self._auto_reconnect = False
        self._health_timer.stop()
        if self._queue:
            self._queue.stop()
            self._queue = None
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        self._active_conn = None
        self.remote_panel.set_disconnected()
        self.connection_changed.emit(None)
        self.status_message.emit("Disconnected")
        return True

    def on_connect_success(self) -> None:
        """Post-connect setup: auto-reconnect, health timer, notify MainWindow."""
        self._auto_reconnect = True
        self._reconnecting = False
        self._health_timer.start()
        self.connection_changed.emit(self._active_conn)

    def _on_connect_failed(self, msg: str) -> None:
        self._active_conn = None
        self._sftp = None
        self.connection_changed.emit(None)
        self.status_message.emit(f"Connection failed: {msg}")

    # ── Queue setup ────────────────────────────────────────────────────────────

    def _setup_queue(self, conn: Connection) -> None:
        if self._queue:
            self._queue.stop()

        def _make_engine() -> TransferEngine:
            client = SFTPClient()
            client.connect(conn)
            return TransferEngine(client)

        self._queue = TransferQueue(
            engine_factory=_make_engine,
            num_workers=4,
            max_retries=5,
            retry_delay=3.0,
        )
        self._queue.on_job_started   = lambda j: self._signals.job_started.emit(j)
        self._queue.on_progress      = lambda j, d, t: self._signals.job_progress.emit(j, d, t)
        self._queue.on_job_done      = lambda j: self._signals.job_done.emit(j)
        self._queue.on_job_failed    = lambda j: self._signals.job_failed.emit(j)
        self._queue.on_job_cancelled = lambda j: self._signals.job_cancelled.emit(j)
        self._queue.on_worker_error  = lambda exc: self._signals.status.emit(f"Worker error: {exc}")
        self._queue.start()

    # ── Auto-reconnect ─────────────────────────────────────────────────────────

    def _check_connection_health(self) -> None:
        if self._sftp is None or not self._auto_reconnect or self._reconnecting:
            return
        if self._sftp.is_alive():
            return
        self._reconnecting = True
        self._signals.reconnecting.emit()
        threading.Thread(target=self._do_reconnect, daemon=True, name="reconnect").start()

    def _do_reconnect(self) -> None:
        delays = [2, 4, 8]
        last_err = ""
        for attempt, delay in enumerate(delays, 1):
            try:
                self._sftp.reconnect(self._active_conn)
                self._setup_queue(self._active_conn)
                self._signals.set_sftp.emit(self._sftp)
                self._signals.reconnected.emit()
                self._reconnecting = False
                return
            except Exception as exc:
                last_err = str(exc)
                if attempt < len(delays):
                    import time
                    time.sleep(delay)
        self._signals.reconnect_failed.emit(last_err)
        self._reconnecting = False

    def _on_reconnecting(self) -> None:
        self.status_message.emit("⟳ Reconnecting…")
        self.reconnect_state_changed.emit()

    def _on_reconnected(self) -> None:
        self.status_message.emit("Reconnected")
        self.remote_panel.refresh()
        self.reconnect_state_changed.emit()

    def _on_reconnect_failed(self, error: str) -> None:
        self.status_message.emit(f"Connection lost: {error}")
        self.reconnect_state_changed.emit()

    # ── Overwrite dialog ──────────────────────────────────────────────────────

    def _on_show_overwrite_dialog(self, filenames: list) -> None:
        """Show conflict dialog on the main thread; release background thread when done."""
        n = len(filenames)
        msg = QMessageBox(self)
        msg.setWindowTitle("File Conflict")
        msg.setIcon(QMessageBox.Icon.Question)

        if n == 1:
            msg.setText(f"<b>{filenames[0]}</b> already exists.")
            msg.setInformativeText("Do you want to overwrite it?")
            overwrite_lbl = "Overwrite"
            skip_lbl      = "Skip"
        else:
            sample = "".join(f"<li>{f}</li>" for f in filenames[:5])
            more   = f"<li><i>… and {n - 5} more</i></li>" if n > 5 else ""
            msg.setText(f"{n} files already exist.")
            msg.setInformativeText(
                f"<ul>{sample}{more}</ul>Do you want to overwrite them?"
            )
            overwrite_lbl = "Overwrite All"
            skip_lbl      = "Skip Existing"

        overwrite_btn = msg.addButton(overwrite_lbl, QMessageBox.ButtonRole.AcceptRole)
        skip_btn      = msg.addButton(skip_lbl,      QMessageBox.ButtonRole.RejectRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.DestructiveRole)
        msg.setDefaultButton(skip_btn)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is overwrite_btn:
            self._overwrite_result = _OVERWRITE_OVERWRITE
        elif clicked is skip_btn:
            self._overwrite_result = _OVERWRITE_SKIP
        else:
            self._overwrite_result = _OVERWRITE_CANCEL

        self._overwrite_event.set()

    def _ask_overwrite(self, conflict_jobs) -> str:
        """Called from the background thread — blocks until the user responds."""
        self._overwrite_event.clear()
        self._signals.show_overwrite_dialog.emit([j.filename for j in conflict_jobs])
        self._overwrite_event.wait()
        return self._overwrite_result

    # ── Transfer callbacks ─────────────────────────────────────────────────────

    def _on_job_done(self, job) -> None:
        self.transfer_panel.job_finished(job)
        self.job_finished.emit(job)
        if self._queue and self._queue.pending_count() == 0:
            self.status_message.emit(f"Done — {job.filename} transferred")
            self._refresh_debounce.start()
        else:
            self.status_message.emit(f"Done: {job.filename}")

    def _on_job_failed(self, job) -> None:
        self.transfer_panel.job_finished(job)
        self.job_finished.emit(job)
        self.status_message.emit(f"Failed: {job.filename} — {job.error or 'unknown'}")

    def _on_job_cancelled(self, job) -> None:
        self.transfer_panel.job_finished(job)
        self.job_finished.emit(job)
        if not job.error:
            self.status_message.emit(f"Cancelled: {job.filename}")

    def _on_pause_resume(self) -> None:
        if not self._queue:
            return
        if self._queue.is_paused():
            self._queue.unpause()
            self.transfer_panel.set_paused(False)
            self.status_message.emit("Transfers resumed")
        else:
            self._queue.pause()
            self.transfer_panel.set_paused(True)
            self.status_message.emit("Transfers paused")

    def _on_cancel(self) -> None:
        if self._queue:
            self._queue.cancel_current()

    def _on_resume(self, job) -> None:
        if self._queue:
            self._queue.enqueue(job)

    # ── Upload (two-phase with conflict detection) ────────────────────────────

    def _on_upload_requested(self, local_paths: list[str], remote_dir: str) -> None:
        if not self._queue:
            QMessageBox.warning(self, "Not connected", "Connect to a server first.")
            return
        n = len(local_paths)
        self._signals.status.emit(
            f"Scanning {n} item{'s' if n > 1 else ''} for upload…"
        )

        def _expand_and_queue():
            conn = self._active_conn
            if conn is None:
                return

            # Phase 1: local walk
            jobs: list[TransferJob] = []
            dirs_needed: set[str] = set()

            for path in local_paths:
                if os.path.isfile(path):
                    remote_path = str(PurePosixPath(remote_dir) / os.path.basename(path))
                    job = TransferJob(
                        local_path=path,
                        remote_path=remote_path,
                        direction=TransferDirection.UPLOAD,
                    )
                    job.total_bytes = os.path.getsize(path)
                    jobs.append(job)
                elif os.path.isdir(path):
                    base = os.path.basename(path)
                    for local_file in sorted(Path(path).rglob("*")):
                        if not local_file.is_file():
                            continue
                        rel = local_file.relative_to(path).as_posix()
                        remote_file = str(PurePosixPath(remote_dir) / base / rel)
                        dirs_needed.add(str(PurePosixPath(remote_file).parent))
                        job = TransferJob(
                            local_path=str(local_file),
                            remote_path=remote_file,
                            direction=TransferDirection.UPLOAD,
                        )
                        job.total_bytes = local_file.stat().st_size
                        jobs.append(job)

            if not jobs:
                self._signals.status.emit("No files found to upload.")
                return

            for job in jobs:
                self._signals.job_enqueued.emit(job)
            self._signals.status.emit(
                f"Found {len(jobs)} file(s) — connecting to remote…"
            )

            # Phase 2: remote prep
            expand_client = SFTPClient()
            try:
                expand_client.connect(conn)
            except Exception as exc:
                for job in jobs:
                    job.state = TransferState.FAILED
                    job.error = str(exc)
                    self._signals.job_failed.emit(job)
                self._signals.status.emit(f"Upload preparation failed: {exc}")
                return

            try:
                self._signals.status.emit(
                    f"Creating remote directories ({len(dirs_needed)})…"
                )
                for d in sorted(dirs_needed):
                    try:
                        expand_client.mkdir_p(d)
                    except Exception:
                        pass

                remote_sizes: dict[str, int] = {}
                dirs_to_check = {
                    str(PurePosixPath(j.remote_path).parent) for j in jobs
                }
                self._signals.status.emit(
                    f"Checking {len(dirs_to_check)} remote director(ies) for conflicts…"
                )
                for d in dirs_to_check:
                    try:
                        for entry in expand_client.listdir(d):
                            if not entry.is_dir:
                                remote_sizes[entry.path] = entry.size
                    except Exception:
                        pass

                unchanged:     list[TransferJob] = []
                conflict_jobs: list[TransferJob] = []
                new_files:     list[TransferJob] = []

                for job in jobs:
                    remote_size = remote_sizes.get(job.remote_path)
                    if remote_size is None:
                        new_files.append(job)
                    elif job.total_bytes > 0 and remote_size == job.total_bytes:
                        unchanged.append(job)
                    else:
                        conflict_jobs.append(job)

                decision = _OVERWRITE_SKIP
                if conflict_jobs:
                    decision = self._ask_overwrite(conflict_jobs)
                    if decision == _OVERWRITE_CANCEL:
                        for job in jobs:
                            job.state = TransferState.CANCELLED
                            self._signals.job_cancelled.emit(job)
                        self._signals.status.emit("Upload cancelled.")
                        return

                if decision == _OVERWRITE_OVERWRITE:
                    enqueue_jobs = new_files + conflict_jobs
                else:
                    enqueue_jobs = new_files

                for job in unchanged:
                    job.state = TransferState.CANCELLED
                    job.error = "up to date"
                    self._signals.job_cancelled.emit(job)
                if decision != _OVERWRITE_OVERWRITE:
                    for job in conflict_jobs:
                        job.state = TransferState.CANCELLED
                        job.error = "skipped (exists)"
                        self._signals.job_cancelled.emit(job)

                for job in enqueue_jobs:
                    self._queue.enqueue(job)

                parts = []
                if unchanged:
                    parts.append(f"{len(unchanged)} up to date")
                if conflict_jobs and decision == _OVERWRITE_SKIP:
                    parts.append(f"{len(conflict_jobs)} existing skipped")
                n_up = len(enqueue_jobs)
                msg = f"Uploading {n_up} file{'s' if n_up != 1 else ''}…"
                if parts:
                    msg += f"  ({', '.join(parts)})"
                self._signals.status.emit(msg)

            finally:
                expand_client.close()

        threading.Thread(target=_expand_and_queue, daemon=True).start()

    # ── Download (with conflict detection) ─────────────────────────────────────

    def _on_download_requested(self, entries) -> None:
        if not self._queue:
            QMessageBox.warning(self, "Not connected", "Connect to a server first.")
            return

        from PySide6.QtWidgets import QFileDialog
        local_dir = QFileDialog.getExistingDirectory(
            self,
            "Download to folder",
            self.local_panel.current_path(),
        )
        if not local_dir:
            return
        self._do_download_to(entries, local_dir)

    def _on_download_drop(self, entry_dicts: list, local_dir: str) -> None:
        """Handle drag-drop from remote panel to local panel."""
        if not self._queue:
            return
        entries = [
            RemoteEntry(
                name=d["name"], path=d["path"],
                is_dir=d["is_dir"], size=d.get("size", 0), mtime=0,
            )
            for d in entry_dicts
        ]
        self._do_download_to(entries, local_dir)

    def _do_download_to(self, entries, local_dir: str) -> None:
        """Download entries to local_dir (shared by drag-drop and menu download)."""
        def _expand_and_queue():
            conn = self._active_conn
            if conn is None:
                return

            expand_client = SFTPClient()
            try:
                expand_client.connect(conn)
            except Exception as exc:
                self._signals.status.emit(f"Download preparation failed: {exc}")
                return

            try:
                jobs: list[TransferJob] = []
                for entry in entries:
                    if not entry.is_dir:
                        local_path = str(Path(local_dir) / entry.name)
                        job = TransferJob(
                            local_path=local_path,
                            remote_path=entry.path,
                            direction=TransferDirection.DOWNLOAD,
                        )
                        job.total_bytes = entry.size
                        jobs.append(job)
                    else:
                        try:
                            remote_files = expand_client.walk(entry.path)
                        except Exception as exc:
                            self._signals.status.emit(
                                f"Could not list {entry.name}: {exc}"
                            )
                            continue
                        base = entry.name
                        for f in remote_files:
                            rel = PurePosixPath(f.path).relative_to(entry.path)
                            local_path = str(Path(local_dir) / base / rel)
                            os.makedirs(os.path.dirname(local_path), exist_ok=True)
                            job = TransferJob(
                                local_path=local_path,
                                remote_path=f.path,
                                direction=TransferDirection.DOWNLOAD,
                            )
                            job.total_bytes = f.size
                            jobs.append(job)

                if not jobs:
                    self._signals.status.emit("No files found to download.")
                    return

                # Overwrite check
                unchanged_dl:     list[TransferJob] = []
                conflict_dl_jobs: list[TransferJob] = []
                new_dl_files:     list[TransferJob] = []

                for job in jobs:
                    if os.path.exists(job.local_path):
                        local_sz = os.path.getsize(job.local_path)
                        if local_sz == job.total_bytes and job.total_bytes > 0:
                            unchanged_dl.append(job)
                        else:
                            conflict_dl_jobs.append(job)
                    else:
                        new_dl_files.append(job)

                decision = _OVERWRITE_SKIP
                if conflict_dl_jobs:
                    decision = self._ask_overwrite(conflict_dl_jobs)
                    if decision == _OVERWRITE_CANCEL:
                        self._signals.status.emit("Download cancelled.")
                        return

                if decision == _OVERWRITE_OVERWRITE:
                    enqueue_dl = new_dl_files + conflict_dl_jobs
                else:
                    enqueue_dl = new_dl_files

                for job in enqueue_dl:
                    self._signals.job_enqueued.emit(job)
                for job in enqueue_dl:
                    self._queue.enqueue(job)

                parts = []
                if unchanged_dl:
                    parts.append(f"{len(unchanged_dl)} up to date")
                if conflict_dl_jobs and decision == _OVERWRITE_SKIP:
                    parts.append(f"{len(conflict_dl_jobs)} existing skipped")
                n_dl = len(enqueue_dl)
                msg = f"Downloading {n_dl} file{'s' if n_dl != 1 else ''}…"
                if parts:
                    msg += f"  ({', '.join(parts)})"
                self._signals.status.emit(msg)

            finally:
                expand_client.close()

        threading.Thread(target=_expand_and_queue, daemon=True).start()

    # ── Sync enqueue ───────────────────────────────────────────────────────────

    def enqueue_sync_jobs(self, jobs: list[TransferJob]) -> None:
        for job in jobs:
            self._signals.job_enqueued.emit(job)
        for job in jobs:
            self._queue.enqueue(job)
        self._signals.status.emit(f"Queued {len(jobs)} sync job(s)")

    # ── Remote copy (same-server, via temp buffer) ─────────────────────────────

    def _on_remote_copy_requested(self, entry_dicts: list, dest_dir: str) -> None:
        conn = self._active_conn
        if conn is None:
            self._signals.status.emit("Not connected — cannot copy.")
            return

        entries = [
            RemoteEntry(
                name=d["name"], path=d["path"],
                is_dir=d["is_dir"], size=d.get("size", 0), mtime=0,
            )
            for d in entry_dicts
        ]

        def _run() -> None:
            tmp_dir = tempfile.mkdtemp(prefix="sftp-ui-copy-")
            try:
                copy_client = SFTPClient()
                copy_client.connect(conn)
            except Exception as exc:
                self._signals.status.emit(f"Remote copy failed (connect): {exc}")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            try:
                transfer_items: list[tuple[str, str, str]] = []
                for entry in entries:
                    if not entry.is_dir:
                        tmp_local = os.path.join(tmp_dir, entry.name)
                        dst_remote = str(PurePosixPath(dest_dir) / entry.name)
                        transfer_items.append((entry.path, dst_remote, tmp_local))
                    else:
                        try:
                            remote_files = copy_client.walk(entry.path)
                        except Exception as exc:
                            self._signals.status.emit(
                                f"Cannot list '{entry.name}': {exc}"
                            )
                            continue
                        base_name = entry.name
                        for f in remote_files:
                            rel = PurePosixPath(f.path).relative_to(entry.path)
                            tmp_local = os.path.join(tmp_dir, base_name, str(rel))
                            os.makedirs(os.path.dirname(tmp_local), exist_ok=True)
                            dst_remote = str(
                                PurePosixPath(dest_dir) / base_name / rel
                            )
                            transfer_items.append((f.path, dst_remote, tmp_local))

                if not transfer_items:
                    self._signals.status.emit("Remote copy: nothing to copy.")
                    return

                n_total = len(transfer_items)
                self._signals.status.emit(
                    f"Copying {n_total} file{'s' if n_total != 1 else ''} "
                    f"to {dest_dir}…"
                )

                errors: list[str] = []
                bytes_copied = 0

                for i, (src_remote, dst_remote, tmp_local) in enumerate(
                    transfer_items, 1
                ):
                    try:
                        dst_parent = str(PurePosixPath(dst_remote).parent)
                        try:
                            copy_client.mkdir_p(dst_parent)
                        except Exception:
                            pass

                        os.makedirs(os.path.dirname(tmp_local) or ".", exist_ok=True)
                        with copy_client.open_remote(src_remote, "rb") as src_fh:
                            with open(tmp_local, "wb") as loc_fh:
                                while True:
                                    chunk = src_fh.read(256 * 1024)
                                    if not chunk:
                                        break
                                    loc_fh.write(chunk)

                        with open(tmp_local, "rb") as loc_fh:
                            with copy_client.open_remote(dst_remote, "wb") as dst_fh:
                                while True:
                                    chunk = loc_fh.read(256 * 1024)
                                    if not chunk:
                                        break
                                    dst_fh.write(chunk)

                        file_size = os.path.getsize(tmp_local)
                        bytes_copied += file_size
                        mb_done = bytes_copied / (1024 * 1024)

                        self._signals.status.emit(
                            f"Copying… {i} / {n_total} files "
                            f"({mb_done:.1f} MB copied)…"
                        )
                    except Exception as exc:
                        errors.append(f"{PurePosixPath(src_remote).name}: {exc}")

                if errors:
                    self._signals.status.emit(
                        f"Copy finished with {len(errors)} error(s): "
                        + ", ".join(errors[:3])
                        + (" …" if len(errors) > 3 else "")
                    )
                else:
                    mb_total = bytes_copied / (1024 * 1024)
                    self._signals.status.emit(
                        f"Copied {n_total} file{'s' if n_total != 1 else ''} "
                        f"({mb_total:.1f} MB) to {dest_dir}"
                    )

            finally:
                copy_client.close()
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self._signals.refresh_remote.emit()

        threading.Thread(target=_run, daemon=True).start()

    # ── Cross-session transfer ──────────────────────────────────────────────

    def _on_cross_session_drop(self, source_session_id: str, entry_dicts: list, dest_dir: str) -> None:
        """Bubble up to MainWindow which can find the source session."""
        self.cross_session_transfer.emit(source_session_id, entry_dicts, dest_dir)

    # ── Terminal ──────────────────────────────────────────────────────────────

    def _on_open_terminal_requested(self, remote_path: str) -> None:
        conn = self._active_conn
        if conn is None:
            self._signals.status.emit("Not connected — cannot open terminal.")
            return

        try:
            open_ssh_terminal(
                host=conn.host,
                user=conn.user,
                port=conn.port,
                remote_path=remote_path,
                key_path=conn.key_path,
            )
            label = remote_path or "~"
            self._signals.status.emit(f"Opened SSH terminal at {label}")
        except Exception as exc:
            self._signals.status.emit(f"Terminal launch failed: {exc}")
