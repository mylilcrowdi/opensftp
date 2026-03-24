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
import threading
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QSplitter, QVBoxLayout, QWidget,
)

from sftp_ui.core.connection import Connection
from sftp_ui.core.sftp_client import SFTPClient
from sftp_ui.core.transfer import TransferDirection, TransferEngine, TransferJob
from sftp_ui.core.queue import TransferQueue
from sftp_ui.ui.panels.local_panel import LocalPanel
from sftp_ui.ui.panels.remote_panel import RemotePanel
from sftp_ui.ui.widgets.transfer_panel import TransferPanel

if TYPE_CHECKING:
    from sftp_ui.core.ui_state import UIState


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


class SessionWidget(QWidget):
    """One SFTP session — owns its own connection, panels, and queue."""

    # Emitted so MainWindow can update the tab title / status dot
    connection_changed = Signal(object)   # Connection or None
    status_message     = Signal(str)

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

        self._build_ui()
        self._connect_signals()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(8, 8, 8, 4)
        cl.setSpacing(4)

        local_path = None
        if self._ui_state:
            local_path = self._ui_state.get_local_path()

        self.local_panel = LocalPanel(initial_path=local_path)
        self.remote_panel = RemotePanel()

        splitter = QSplitter()
        splitter.addWidget(self.local_panel)
        splitter.addWidget(self.remote_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        cl.addWidget(splitter, stretch=1)

        self.transfer_panel = TransferPanel()
        self.transfer_panel.cancel_requested.connect(self._on_cancel)
        self.transfer_panel.resume_requested.connect(self._on_resume)
        self.transfer_panel.pause_resume_requested.connect(self._on_pause_resume)
        cl.addWidget(self.transfer_panel)

        layout.addWidget(content)

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

        self.remote_panel.upload_requested.connect(self._on_upload_requested)
        self.remote_panel.download_requested.connect(self._on_download_requested)
        self.local_panel.download_drop_requested.connect(self._on_download_drop)
        self.remote_panel.status_message.connect(self.status_message)
        self.local_panel.status_message.connect(self.status_message)

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

            remote_path = "/"
            if self._ui_state:
                saved = self._ui_state.get_remote_path()
                if saved:
                    remote_path = saved
            self._signals.navigate_remote.emit(remote_path)

        threading.Thread(target=_do_connect, daemon=True).start()

    def disconnect(self) -> bool:
        """Disconnect from server. Returns False if user cancelled."""
        from PySide6.QtWidgets import QMessageBox
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
        """Called by MainWindow after connect_success signal."""
        self._auto_reconnect = True
        self._reconnecting = False
        self._health_timer.start()
        self.connection_changed.emit(self._active_conn)

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

    def _on_reconnected(self) -> None:
        self.status_message.emit("Reconnected")
        self.remote_panel.refresh()

    def _on_reconnect_failed(self, error: str) -> None:
        self.status_message.emit(f"Connection lost: {error}")

    # ── Transfer callbacks ─────────────────────────────────────────────────────

    def _on_job_done(self, job) -> None:
        self.transfer_panel.job_finished(job)
        if self._queue and self._queue.pending_count() == 0:
            self.status_message.emit(f"Done — {job.filename} transferred")
            self._refresh_debounce.start()
        else:
            self.status_message.emit(f"Done: {job.filename}")

    def _on_job_failed(self, job) -> None:
        self.transfer_panel.job_finished(job)
        self.status_message.emit(f"Failed: {job.filename} — {job.error or 'unknown'}")

    def _on_job_cancelled(self, job) -> None:
        self.transfer_panel.job_finished(job)
        label = job.error or "Cancelled"
        self.status_message.emit(f"{job.filename}: {label}")

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

    # ── Upload / Download ──────────────────────────────────────────────────────

    def _on_upload_requested(self, local_paths: list[str], remote_dir: str) -> None:
        if not self._queue or not self._active_conn:
            return

        def _expand_and_queue():
            conn = self._active_conn
            if not conn:
                return
            expand_client = SFTPClient()
            try:
                expand_client.connect(conn)
            except Exception as exc:
                self._signals.status.emit(f"Upload preparation failed: {exc}")
                return

            try:
                jobs: list[TransferJob] = []
                for local_path in local_paths:
                    p = Path(local_path)
                    if p.is_file():
                        remote_path = f"{remote_dir.rstrip('/')}/{p.name}"
                        job = TransferJob(
                            local_path=str(p),
                            remote_path=remote_path,
                            direction=TransferDirection.UPLOAD,
                        )
                        job.total_bytes = p.stat().st_size
                        jobs.append(job)
                    elif p.is_dir():
                        for child in p.rglob("*"):
                            if child.is_file():
                                rel = child.relative_to(p.parent)
                                remote_path = f"{remote_dir.rstrip('/')}/{rel.as_posix()}"
                                job = TransferJob(
                                    local_path=str(child),
                                    remote_path=remote_path,
                                    direction=TransferDirection.UPLOAD,
                                )
                                job.total_bytes = child.stat().st_size
                                jobs.append(job)

                if not jobs:
                    self._signals.status.emit("No files to upload.")
                    return

                # Create remote directories
                dirs_needed = set()
                for job in jobs:
                    dirs_needed.add(str(PurePosixPath(job.remote_path).parent))
                for d in sorted(dirs_needed):
                    try:
                        expand_client.mkdir_p(d)
                    except Exception:
                        pass

                for job in jobs:
                    self._signals.job_enqueued.emit(job)
                for job in jobs:
                    self._queue.enqueue(job)

                n = len(jobs)
                self._signals.status.emit(f"Uploading {n} file{'s' if n != 1 else ''}…")
            finally:
                expand_client.close()

        threading.Thread(target=_expand_and_queue, daemon=True).start()

    def _on_download_requested(self, entries) -> None:
        if not self._queue:
            return
        from PySide6.QtWidgets import QFileDialog
        local_dir = QFileDialog.getExistingDirectory(
            self, "Download to folder", self.local_panel.current_path(),
        )
        if not local_dir:
            return
        self._do_download_to(entries, local_dir)

    def _on_download_drop(self, entry_dicts: list, local_dir: str) -> None:
        if not self._queue:
            return
        from sftp_ui.core.sftp_client import RemoteEntry
        entries = [
            RemoteEntry(
                name=d["name"], path=d["path"],
                is_dir=d["is_dir"], size=d.get("size", 0), mtime=0,
            )
            for d in entry_dicts
        ]
        self._do_download_to(entries, local_dir)

    def _do_download_to(self, entries, local_dir: str) -> None:
        def _expand_and_queue():
            conn = self._active_conn
            if not conn:
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
                            self._signals.status.emit(f"Could not list {entry.name}: {exc}")
                            continue
                        for f in remote_files:
                            rel = PurePosixPath(f.path).relative_to(entry.path)
                            local_path = str(Path(local_dir) / entry.name / rel)
                            os.makedirs(os.path.dirname(local_path), exist_ok=True)
                            job = TransferJob(
                                local_path=local_path,
                                remote_path=f.path,
                                direction=TransferDirection.DOWNLOAD,
                            )
                            job.total_bytes = f.size
                            jobs.append(job)

                if not jobs:
                    self._signals.status.emit("No files to download.")
                    return

                for job in jobs:
                    self._signals.job_enqueued.emit(job)
                for job in jobs:
                    self._queue.enqueue(job)

                n = len(jobs)
                self._signals.status.emit(f"Downloading {n} file{'s' if n != 1 else ''}…")
            finally:
                expand_client.close()

        threading.Thread(target=_expand_and_queue, daemon=True).start()
