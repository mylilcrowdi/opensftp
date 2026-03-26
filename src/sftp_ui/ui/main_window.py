"""
MainWindow — composes all panels and wires up the application.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path, PurePosixPath
from typing import Optional, TYPE_CHECKING

_OVERWRITE_OVERWRITE = "overwrite"
_OVERWRITE_SKIP      = "skip"
_OVERWRITE_CANCEL    = "cancel"

from PySide6.QtCore import Qt, QByteArray, QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from sftp_ui.core.connection import Connection, ConnectionStore
from sftp_ui.core.platform_utils import open_ssh_terminal
from sftp_ui.core.sftp_client import AuthenticationError, ConnectionError, SFTPClient
from sftp_ui.core.transfer import TransferDirection, TransferEngine, TransferJob
from sftp_ui.core.queue import TransferQueue
from sftp_ui.core.transfer_history import TransferHistory
from sftp_ui.core.ui_state import UIState
from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
from sftp_ui.ui.dialogs.connection_manager import ConnectionManagerDialog
from sftp_ui.ui.dialogs.command_palette import CommandPaletteDialog
from sftp_ui.ui.dialogs.shortcuts_dialog import ShortcutsDialog
from sftp_ui.core.command_registry import Command, CommandRegistry
from sftp_ui.ui.dialogs.sync_dialog import SyncDialog
from sftp_ui.ui.panels.local_panel import LocalPanel
from sftp_ui.ui.panels.remote_panel import RemotePanel
from sftp_ui.ui.widgets.transfer_panel import TransferPanel
from sftp_ui.ui.widgets.status_dot import StatusDot
from sftp_ui.ui.widgets.animated_status_bar import AnimatedStatusBar
from sftp_ui.ui.widgets.bookmarks_bar import BookmarksBar
from sftp_ui.animations.transitions import fade_in

if TYPE_CHECKING:
    from sftp_ui.styling.theme_manager import ThemeManager


# ── Thread-safe signal bridge ─────────────────────────────────────────────────

class _Signals(QObject):
    status          = Signal(str)
    job_enqueued    = Signal(object)           # TransferJob — pre-registers in panel
    job_started     = Signal(object)           # TransferJob — worker picked it up
    job_progress    = Signal(object, int, int) # TransferJob, done, total
    job_done        = Signal(object)           # TransferJob
    job_failed      = Signal(object)           # TransferJob
    job_cancelled   = Signal(object)           # TransferJob
    refresh_remote         = Signal()
    navigate_remote        = Signal(str)       # navigate to specific path
    connect_success        = Signal()
    connect_failed         = Signal(str)
    show_overwrite_dialog  = Signal(list)      # list[str] conflict filenames → ask user
    set_sftp               = Signal(object)    # SFTPClient — hand-off to remote panel (main thread)
    reconnecting           = Signal()          # health-check detected drop
    reconnected            = Signal()          # reconnection succeeded
    reconnect_failed       = Signal(str)       # reconnection failed after retries


# ── MainWindow ────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(
        self,
        store: Optional[ConnectionStore] = None,
        theme_manager: Optional["ThemeManager"] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("SFTP UI")
        self.resize(1200, 750)
        self.setMinimumSize(900, 600)

        self._store = store or ConnectionStore()
        self._sftp: Optional[SFTPClient] = None
        self._queue: Optional[TransferQueue] = None
        self._signals = _Signals()
        self._theme_manager = theme_manager
        self._ui_state = UIState()
        self._active_conn: Optional[Connection] = None
        self._show_connect_error_dialog: bool = True   # False during auto-reconnect

        # Auto-reconnect state
        self._auto_reconnect: bool = False      # True while connected; False on intentional disconnect
        self._reconnecting: bool = False         # prevents concurrent reconnect attempts

        # Overwrite-conflict resolution — background thread blocks on this event
        # while the main thread shows the dialog and sets the result.
        self._overwrite_event: threading.Event = threading.Event()
        self._overwrite_result: str = _OVERWRITE_CANCEL

        # Persistent transfer history
        self._history = TransferHistory(Path.home() / ".config" / "sftp-ui" / "transfer_history.jsonl")

        self._command_registry = CommandRegistry()

        self._build_ui()
        self._connect_signals()
        self._register_commands()
        self._reload_connection_list()
        self._restore_session()
        self._restore_geometry()
        QTimer.singleShot(0, self._maybe_auto_connect)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        # Bookmarks bar — auto-hides when no favorites are starred
        self._bookmarks_bar = BookmarksBar(self._store, parent=central)
        self._bookmarks_bar.connect_requested.connect(self._on_bookmark_connect)
        root.addWidget(self._bookmarks_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._local_panel = LocalPanel(initial_path=self._ui_state.local_path())
        self._remote_panel = RemotePanel()
        splitter.addWidget(self._local_panel)
        splitter.addWidget(self._remote_panel)
        splitter.setSizes([380, 820])
        splitter.setHandleWidth(1)
        # Prevent either panel from being collapsed to zero by the splitter
        self._local_panel.setMinimumWidth(200)
        self._remote_panel.setMinimumWidth(200)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(8, 8, 8, 4)
        cl.setSpacing(4)
        cl.addWidget(splitter, stretch=1)

        self._transfer_panel = TransferPanel()
        self._transfer_panel.cancel_requested.connect(self._on_cancel)
        self._transfer_panel.resume_requested.connect(self._on_resume)
        self._transfer_panel.pause_resume_requested.connect(self._on_pause_resume)
        cl.addWidget(self._transfer_panel)

        # Debounce remote refresh: parallel workers can finish within milliseconds
        # of each other; without debounce every last-job callback triggers a
        # separate listdir round-trip.  300 ms window collapses them into one.
        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(300)
        self._refresh_debounce.timeout.connect(self._remote_panel.refresh)

        # Connection health check — polls is_alive() every 10 s
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(10_000)
        self._health_timer.timeout.connect(self._check_connection_health)

        root.addWidget(content, stretch=1)

        self._status = AnimatedStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready")

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(46)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(6)

        self._conn_combo = QComboBox()
        self._conn_combo.setMinimumWidth(200)
        self._conn_combo.setMaximumWidth(320)  # prevent very long names from crushing toolbar
        self._conn_combo.setPlaceholderText("Select connection…")
        self._conn_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._status_dot = StatusDot()

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setObjectName("primary")
        self._connect_btn.setToolTip("Connect to the selected server (Ctrl+K)")

        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.setToolTip("Disconnect from the current server (Ctrl+K)")

        sep1 = QLabel("|")
        sep1.setStyleSheet("color: #45475a; padding: 0 4px;")

        new_btn    = QPushButton("+ New")
        new_btn.setToolTip("Create a new connection (Ctrl+N)")
        edit_btn   = QPushButton("✎ Edit")
        edit_btn.setToolTip("Edit the selected connection")
        delete_btn = QPushButton("✕ Delete")
        delete_btn.setObjectName("danger")
        delete_btn.setToolTip("Delete the selected connection")
        manage_btn = QPushButton("⚙ Manage")
        manage_btn.setToolTip("Open the connection manager")

        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #45475a; padding: 0 4px;")

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setToolTip("Refresh the remote file listing (F5 or Ctrl+R)")

        self._sync_btn = QPushButton("⇄ Sync")
        self._sync_btn.setEnabled(False)
        self._sync_btn.setToolTip("Compare local and remote directories")

        for w in (
            self._conn_combo,
            self._status_dot,
            self._connect_btn, self._disconnect_btn,
            sep1,
            new_btn, edit_btn, delete_btn, manage_btn,
            sep2,
            self._refresh_btn, self._sync_btn,
        ):
            row.addWidget(w)
        row.addStretch()

        if self._theme_manager:
            self._theme_btn = QPushButton("🎨 Theme")
            self._theme_btn.setToolTip("Choose appearance theme  (Dark / Light / Nord / Dracula …)")
            self._theme_btn.clicked.connect(self._on_open_theme_dialog)
            row.addWidget(self._theme_btn)

        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        self._sync_btn.clicked.connect(self._on_sync)
        new_btn.clicked.connect(self._on_new_connection)
        edit_btn.clicked.connect(self._on_edit_connection)
        delete_btn.clicked.connect(self._on_delete_connection)
        manage_btn.clicked.connect(self._on_manage_connections)
        # Pressing Enter while the combo is focused (keyboard navigation) triggers connect
        self._conn_combo.activated.connect(lambda _idx: self._on_connect())

        return bar

    def _connect_signals(self) -> None:
        self._remote_panel.upload_requested.connect(self._on_upload_requested)
        self._remote_panel.download_requested.connect(self._on_download_requested)
        self._remote_panel.remote_copy_requested.connect(self._on_remote_copy_requested)
        self._remote_panel.open_terminal_requested.connect(self._on_open_terminal_requested)
        self._local_panel.download_drop_requested.connect(self._on_download_drop)
        self._remote_panel.status_message.connect(self._status.showMessage)
        self._local_panel.status_message.connect(self._status.showMessage)
        self._refresh_btn.clicked.connect(self._remote_panel.refresh)

        # Persist navigation state
        self._local_panel.path_changed.connect(self._ui_state.set_local_path)
        self._remote_panel.path_changed.connect(self._on_remote_path_changed)

        sig = self._signals
        sig.status.connect(self._status.showMessage)
        sig.job_enqueued.connect(self._transfer_panel.add_job)
        sig.job_started.connect(self._transfer_panel.refresh_job)
        sig.job_progress.connect(self._transfer_panel.update_progress)
        sig.job_done.connect(self._on_job_done)
        sig.job_failed.connect(self._on_job_failed)
        sig.job_cancelled.connect(self._on_job_cancelled)
        sig.refresh_remote.connect(self._remote_panel.refresh)
        sig.navigate_remote.connect(self._remote_panel.navigate_or_root)
        sig.connect_success.connect(self._on_connect_success)
        sig.connect_failed.connect(self._on_connect_failed)
        sig.show_overwrite_dialog.connect(self._on_show_overwrite_dialog)
        sig.set_sftp.connect(self._remote_panel.set_sftp)
        sig.reconnecting.connect(self._on_reconnecting)
        sig.reconnected.connect(self._on_reconnected)
        sig.reconnect_failed.connect(self._on_reconnect_failed)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._remote_panel.refresh)
        QShortcut(QKeySequence("F5"),     self).activated.connect(self._remote_panel.refresh)
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._toggle_connection)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._on_new_connection)
        QShortcut(QKeySequence("Ctrl+Shift+."), self).activated.connect(self._remote_panel.toggle_hidden)
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(self._remote_panel.focus_path_input)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._toggle_bookmarks_bar)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._on_search)
        QShortcut(QKeySequence("Ctrl+P"),      self).activated.connect(self._show_command_palette)
        QShortcut(QKeySequence("F1"),          self).activated.connect(self._show_shortcuts_dialog)
        QShortcut(QKeySequence("Ctrl+?"),      self).activated.connect(self._show_shortcuts_dialog)

        self._remote_panel.column_widths_changed.connect(
            lambda widths: self._ui_state.set_column_widths("remote", widths)
        )

        # Persist sort state for both panels
        self._remote_panel.sort_state_changed.connect(
            lambda col, order: self._ui_state.set_sort_state("remote", col, order)
        )
        self._local_panel.sort_state_changed.connect(
            lambda col, order: self._ui_state.set_sort_state("local", col, order)
        )

        # Restore local panel sort immediately (it has data at startup)
        local_sort_col, local_sort_order = self._ui_state.get_sort_state("local")
        if local_sort_col != -1:
            self._local_panel.restore_sort_state(local_sort_col, local_sort_order)

    # ── Session restore ───────────────────────────────────────────────────────

    def _restore_session(self) -> None:
        """Pre-select the last used connection in the combo box."""
        saved_id = self._ui_state.last_connection_id
        if not saved_id:
            return
        for i in range(self._conn_combo.count()):
            if self._conn_combo.itemData(i) == saved_id:
                self._conn_combo.setCurrentIndex(i)
                return
        # Connection was deleted — clear stale flags.
        self._ui_state.last_connection_id = None
        self._ui_state.set_was_connected(False)

    def _maybe_auto_connect(self) -> None:
        """Fire a single reconnect attempt if the last session was active.

        Runs in the first event-loop tick so the window is fully rendered first.
        Delegates entirely to _on_connect — no duplicated connection logic.

        Edge cases handled:
          - was_connected False (clean exit / never connected): no-op
          - last_connection_id missing or deleted: clear flag, no-op
          - combo pre-selection no longer matches (rare race): abort
          - server unreachable / bad credentials: _on_connect_failed clears flag
        """
        if not self._ui_state.was_connected:
            return

        conn_id = self._ui_state.last_connection_id
        if not conn_id:
            self._ui_state.set_was_connected(False)
            return

        try:
            conn = self._store.get(conn_id)
        except KeyError:
            # Connection was deleted after the last session.
            self._ui_state.set_was_connected(False)
            return

        # Verify the combo still shows this connection (defensive — should always
        # match after _restore_session, but guards against future timing changes).
        selected = self._selected_connection()
        if selected is None or selected.id != conn_id:
            self._ui_state.set_was_connected(False)
            return

        self._status.showMessage(f"Reconnecting to {conn.name}…")
        self._show_connect_error_dialog = False
        self._on_connect()
        self._show_connect_error_dialog = True

    def _on_remote_path_changed(self, path: str) -> None:
        if self._active_conn:
            self._ui_state.set_remote_path(self._active_conn.id, path)

    # ── Connection ────────────────────────────────────────────────────────────

    def _reload_connection_list(self) -> None:
        self._conn_combo.clear()
        conns = self._store.all()
        # Favorites first, then by group, then by name
        favorites = sorted([c for c in conns if c.favorite],     key=lambda c: c.name.lower())
        others    = sorted([c for c in conns if not c.favorite], key=lambda c: (c.group.lower(), c.name.lower()))
        for conn in favorites + others:
            label = f"★ {conn.name}" if conn.favorite else conn.name
            if conn.group:
                label += f"  [{conn.group}]"
            self._conn_combo.addItem(label, conn.id)
        # Keep the bookmarks bar in sync with the store
        if hasattr(self, "_bookmarks_bar"):
            self._bookmarks_bar.refresh()

    def _selected_connection(self) -> Optional[Connection]:
        idx = self._conn_combo.currentIndex()
        if idx < 0:
            return None
        conn_id = self._conn_combo.itemData(idx)
        try:
            return self._store.get(conn_id)
        except KeyError:
            return None

    def _toggle_connection(self) -> None:
        if self._sftp:
            self._on_disconnect()
        else:
            self._on_connect()

    def _on_connect(self) -> None:
        conn = self._selected_connection()
        if not conn:
            QMessageBox.warning(self, "No connection", "Please select or create a connection.")
            return
        self._status.showMessage(f"Connecting to {conn.host}…")
        self._connect_btn.setEnabled(False)
        self._status_dot.set_connecting()
        self._active_conn = conn
        self._ui_state.set_last_connection(conn.id)

        saved_remote = self._ui_state.remote_path(conn.id)

        def _do():
            sftp = SFTPClient()
            try:
                sftp.connect(conn)
            except (AuthenticationError, ConnectionError) as exc:
                self._signals.connect_failed.emit(str(exc))
                return
            self._sftp = sftp
            self._setup_queue(conn)
            # Dispatch set_sftp to the main thread via signal — QWidget methods
            # must never be called from a background thread.
            self._signals.set_sftp.emit(sftp)
            self._signals.connect_success.emit()
            self._signals.status.emit(f"Connected to {conn.host}")
            self._signals.navigate_remote.emit(saved_remote)

        threading.Thread(target=_do, daemon=True).start()

    def _on_connect_success(self) -> None:
        self._ui_state.set_was_connected(True)
        self._auto_reconnect = True
        self._reconnecting = False
        self._health_timer.start()
        # Record last_connected timestamp for connection manager display
        if self._active_conn:
            try:
                self._store.record_connected(self._active_conn.id)
            except Exception:
                pass
            self.setWindowTitle(f"SFTP UI — {self._active_conn.name}")
        self._status_dot.set_connected()
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._sync_btn.setEnabled(True)
        saved_widths = self._ui_state.get_column_widths("remote")
        if saved_widths:
            self._remote_panel.set_column_widths(saved_widths)

        # Restore sort state — applied before data arrives so _apply_entries
        # will honour the saved column on the first listing.
        remote_sort_col, remote_sort_order = self._ui_state.get_sort_state("remote")
        self._remote_panel.restore_sort_state(remote_sort_col, remote_sort_order)

    def _on_connect_failed(self, msg: str) -> None:
        self._ui_state.set_was_connected(False)
        # Reset all connection state so the UI is fully in "disconnected" mode.
        # _active_conn must be cleared so stale references can't be used.
        self._active_conn = None
        self._sftp = None
        self._status_dot.set_failed()      # red dot — last attempt failed
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._sync_btn.setEnabled(False)
        self.setWindowTitle("SFTP UI")
        self._status.showMessage(f"Connection failed: {msg}")
        if self._show_connect_error_dialog and self._active_conn:
            from PySide6.QtWidgets import QMessageBox
            err = QMessageBox(self)
            err.setWindowTitle("Connection Failed")
            err.setIcon(QMessageBox.Icon.Critical)
            err.setText(
                f"<b>Could not connect to {self._active_conn.host}:{self._active_conn.port}</b>"
            )
            err.setInformativeText(msg)
            err.setStandardButtons(QMessageBox.StandardButton.Ok)
            err.exec()

    def _setup_queue(self, conn: Connection) -> None:
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
        self._queue.on_worker_error  = lambda exc: self._signals.status.emit(
            f"Worker connection failed: {exc}"
        )
        self._queue.start()

    def _on_disconnect(self) -> None:
        # Warn the user if transfers are still running so they don't lose work.
        if self._queue and self._queue.pending_count() > 0:
            n = self._queue.pending_count()
            reply = QMessageBox.question(
                self,
                "Transfers in progress",
                f"{n} transfer{'s are' if n != 1 else ' is'} still in progress.\n"
                "Disconnecting will cancel them. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._auto_reconnect = False
        self._health_timer.stop()
        self._ui_state.set_was_connected(False)
        self._status_dot.set_idle()
        if self._queue:
            self._queue.stop()
            self._queue = None
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        self._active_conn = None
        self.setWindowTitle("SFTP UI")
        self._remote_panel.set_disconnected()
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._sync_btn.setEnabled(False)
        self._status.showMessage("Disconnected")

    # ── Auto-reconnect ────────────────────────────────────────────────────────

    def _check_connection_health(self) -> None:
        """Called every 10 s by _health_timer.  Triggers reconnect on drop."""
        if (
            self._sftp is None
            or not self._auto_reconnect
            or self._reconnecting
        ):
            return
        if self._sftp.is_alive():
            return

        self._reconnecting = True
        self._signals.reconnecting.emit()
        threading.Thread(
            target=self._do_reconnect, daemon=True, name="reconnect"
        ).start()

    def _do_reconnect(self) -> None:
        """Background thread: attempt reconnection with exponential backoff."""
        delays = [2, 4, 8]  # seconds between attempts
        last_err = ""
        for attempt, delay in enumerate(delays, 1):
            try:
                self._sftp.reconnect(self._active_conn)
                # Success — re-create the queue with a fresh connection
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
        self._status_dot.set_idle()  # amber-ish neutral state
        self._status.showMessage("⟳ Reconnecting…")

    def _on_reconnected(self) -> None:
        self._status_dot.set_connected()
        self._status.showMessage("Reconnected")
        self._remote_panel.refresh()

    def _on_reconnect_failed(self, error: str) -> None:
        self._status_dot.set_failed()
        self._status.showMessage(f"Connection lost: {error}")

    def _on_new_connection(self) -> None:
        dlg = ConnectionDialog(self, store=self._store)
        if dlg.exec():
            conn = dlg.result_connection()
            self._store.add(conn)
            self._reload_connection_list()
            for i in range(self._conn_combo.count()):
                if self._conn_combo.itemData(i) == conn.id:
                    self._conn_combo.setCurrentIndex(i)
                    break

    def _on_edit_connection(self) -> None:
        conn = self._selected_connection()
        if not conn:
            return
        dlg = ConnectionDialog(self, conn=conn, store=self._store)
        if dlg.exec():
            self._store.update(dlg.result_connection())
            self._reload_connection_list()

    def _on_manage_connections(self) -> None:
        def _do_connect(conn: Connection) -> None:
            # Select the connection in the combo and trigger connect
            for i in range(self._conn_combo.count()):
                if self._conn_combo.itemData(i) == conn.id:
                    self._conn_combo.setCurrentIndex(i)
                    break
            self._on_connect()

        dlg = ConnectionManagerDialog(self._store, on_connect=_do_connect, parent=self)
        dlg.exec()
        self._reload_connection_list()

    def _on_bookmark_connect(self, conn: Connection) -> None:
        """Handle a click on a bookmarks-bar chip: select in combo + connect."""
        for i in range(self._conn_combo.count()):
            if self._conn_combo.itemData(i) == conn.id:
                self._conn_combo.setCurrentIndex(i)
                break
        self._on_connect()

    def _toggle_bookmarks_bar(self) -> None:
        """Ctrl+B: toggle the bookmarks bar visibility manually."""
        if self._bookmarks_bar.isVisible():
            self._bookmarks_bar.setVisible(False)
        else:
            self._bookmarks_bar.refresh()  # ensure it's current before showing

    def _on_delete_connection(self) -> None:
        conn = self._selected_connection()
        if not conn:
            return
        if QMessageBox.question(
            self, "Delete connection", f"Delete '{conn.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self._store.remove(conn.id)
            self._reload_connection_list()

    # ── Overwrite dialog (main-thread handler) ────────────────────────────────

    def _on_show_overwrite_dialog(self, filenames: list) -> None:
        """Show conflict dialog on the main thread; release background thread when done."""
        n = len(filenames)
        msg = QMessageBox(self)
        msg.setWindowTitle("File Conflict")
        msg.setIcon(QMessageBox.Icon.Question)

        if n == 1:
            msg.setText(f"<b>{filenames[0]}</b> already exists on the server.")
            msg.setInformativeText("Do you want to overwrite it?")
            overwrite_lbl = "Overwrite"
            skip_lbl      = "Skip"
        else:
            sample = "".join(f"<li>{f}</li>" for f in filenames[:5])
            more   = f"<li><i>… and {n - 5} more</i></li>" if n > 5 else ""
            msg.setText(f"{n} files already exist on the server.")
            msg.setInformativeText(
                f"<ul>{sample}{more}</ul>Do you want to overwrite them?"
            )
            overwrite_lbl = "Overwrite All"
            skip_lbl      = "Skip Existing"

        overwrite_btn = msg.addButton(overwrite_lbl, QMessageBox.ButtonRole.AcceptRole)
        skip_btn      = msg.addButton(skip_lbl,      QMessageBox.ButtonRole.RejectRole)
        cancel_btn    = msg.addButton("Cancel",       QMessageBox.ButtonRole.DestructiveRole)
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

    # ── Transfers ─────────────────────────────────────────────────────────────

    def _on_upload_requested(self, local_paths: list[str], remote_dir: str) -> None:
        if not self._queue:
            QMessageBox.warning(self, "Not connected", "Connect to a server first.")
            return
        n = len(local_paths)
        self._status.showMessage(
            f"Scanning {n} item{'s' if n > 1 else ''} for upload…"
        )

        def _expand_and_queue():
            """Two-phase upload preparation.

            Phase 1 — local walk (no network, <100 ms for any tree):
              Walk the local paths, build TransferJob objects, and immediately
              pre-register every job in the panel.  The user sees the full file
              list right away instead of waiting for the remote round-trips.

            Phase 2 — remote prep (network, proportional to depth):
              Connect a dedicated SFTPClient, create missing remote directories,
              list existing files to detect conflicts, resolve conflicts via a
              dialog, then enqueue only the jobs that should be transferred.
              Jobs that will be skipped are cancelled in the panel so the queue
              eventually settles and hides cleanly.
            """
            conn = self._active_conn
            if conn is None:
                return

            # ── Phase 1: local walk ────────────────────────────────────────────
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

            # Immediately show ALL discovered jobs in the panel — panel appears
            # before any network I/O starts.
            for job in jobs:
                self._signals.job_enqueued.emit(job)
            self._signals.status.emit(
                f"Found {len(jobs)} file(s) — connecting to remote…"
            )

            # ── Phase 2: remote prep ───────────────────────────────────────────
            expand_client = SFTPClient()
            try:
                expand_client.connect(conn)
            except Exception as exc:
                # Cancel every pre-registered job so the panel settles and hides.
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

                # One listdir per unique parent dir to detect conflicts.
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

                # Categorise
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

                # Resolve conflicts on main thread (blocks this thread briefly).
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
                    skip_jobs    = unchanged
                else:
                    enqueue_jobs = new_files
                    skip_jobs    = unchanged + conflict_jobs

                # Cancel pre-registered jobs that will not be transferred,
                # so the panel settles and the queue hides cleanly.
                for job in unchanged:
                    job.state = TransferState.CANCELLED
                    job.error = "up to date"
                    self._signals.job_cancelled.emit(job)
                if decision != _OVERWRITE_OVERWRITE:
                    for job in conflict_jobs:
                        job.state = TransferState.CANCELLED
                        job.error = "skipped (exists)"
                        self._signals.job_cancelled.emit(job)

                # Enqueue the jobs that should actually be transferred.
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

    def _on_download_requested(self, entries) -> None:
        if not self._queue:
            QMessageBox.warning(self, "Not connected", "Connect to a server first.")
            return

        from PySide6.QtWidgets import QFileDialog
        local_dir = QFileDialog.getExistingDirectory(
            self,
            "Download to folder",
            self._local_panel.current_path(),
        )
        if not local_dir:
            return  # user cancelled

        def _expand_and_queue_download():
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
                        # Recursively walk the remote directory.
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

                # ── Overwrite check ────────────────────────────────────────────
                # Classify jobs by whether the local file already exists.
                # "unchanged" = same size → the engine will skip anyway.
                # "conflict"  = different size → would overwrite/truncate.
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
                    skip_dl    = unchanged_dl
                else:
                    enqueue_dl = new_dl_files
                    skip_dl    = unchanged_dl + conflict_dl_jobs

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

        threading.Thread(target=_expand_and_queue_download, daemon=True).start()

    def _on_download_drop(self, entry_dicts: list, local_dir: str) -> None:
        """Handle drag-drop from remote panel to local panel (download)."""
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
        # Reuse the download handler but with pre-selected local_dir
        # (skip QFileDialog since user chose the target by dropping)
        self._do_download_to(entries, local_dir)

    def _on_open_terminal_requested(self, remote_path: str) -> None:
        """Open the OS terminal emulator with an SSH session pre-cd'd into *remote_path*.

        Reads host/user/port/key_path from the active connection and delegates
        to :func:`~sftp_ui.core.platform_utils.open_ssh_terminal` which handles
        macOS (Terminal.app), Windows (Windows Terminal / cmd.exe), and the most
        common Linux terminal emulators.

        Args:
            remote_path: The remote directory path to ``cd`` into after login.
        """
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

    def _on_remote_copy_requested(self, entry_dicts: list, dest_dir: str) -> None:
        """Handle drag-drop between two remote locations (same server, temp-buffer copy).

        Each source entry is streamed through a local temp directory:
          1. Download source → temp file (via open_remote stream)
          2. Upload temp file → destination (via open_remote stream)
          3. Clean up the temp directory when all copies finish.

        Directories are copied recursively by walking their contents first.

        Args:
            entry_dicts: Serialised remote entries from the drag MIME payload.
            dest_dir:    Target directory on the remote server.
        """
        conn = self._active_conn
        if conn is None:
            self._signals.status.emit("Not connected — cannot copy.")
            return

        import tempfile
        import shutil
        from sftp_ui.core.sftp_client import RemoteEntry

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
                # Collect all (src_remote_path, dst_remote_path, tmp_local_path) triples
                transfer_items: list[tuple[str, str, str]] = []
                for entry in entries:
                    if not entry.is_dir:
                        tmp_local = os.path.join(tmp_dir, entry.name)
                        dst_remote = str(PurePosixPath(dest_dir) / entry.name)
                        transfer_items.append((entry.path, dst_remote, tmp_local))
                    else:
                        # Walk the source directory and mirror the tree
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
                total_bytes = sum(
                    e.size for e in entries if not e.is_dir
                )
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
                        # Step 1: ensure destination parent directory exists
                        dst_parent = str(PurePosixPath(dst_remote).parent)
                        try:
                            copy_client.mkdir_p(dst_parent)
                        except Exception:
                            pass  # may already exist

                        # Step 2: stream src → tmp_local
                        os.makedirs(os.path.dirname(tmp_local) or ".", exist_ok=True)
                        with copy_client.open_remote(src_remote, "rb") as src_fh:
                            with open(tmp_local, "wb") as loc_fh:
                                while True:
                                    chunk = src_fh.read(256 * 1024)
                                    if not chunk:
                                        break
                                    loc_fh.write(chunk)

                        # Step 3: stream tmp_local → dst_remote
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

                # Final summary
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
                            self._signals.status.emit(f"Could not list {entry.name}: {exc}")
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

                for job in jobs:
                    self._signals.job_enqueued.emit(job)
                for job in jobs:
                    self._queue.enqueue(job)

                n = len(jobs)
                self._signals.status.emit(
                    f"Downloading {n} file{'s' if n != 1 else ''}…"
                )
            finally:
                expand_client.close()

        threading.Thread(target=_expand_and_queue, daemon=True).start()

    def _on_job_done(self, job) -> None:
        self._transfer_panel.job_finished(job)
        self._history.record(job)
        if self._queue and self._queue.pending_count() == 0:
            self._status.showMessage(f"Done — {job.filename} transferred")
            self._refresh_debounce.start()
        else:
            self._status.showMessage(f"Done: {job.filename}")

    def _on_job_failed(self, job) -> None:
        self._transfer_panel.job_finished(job)
        self._history.record(job)
        self._status.showMessage(f"Failed: {job.filename} — {job.error}")

    def _on_job_cancelled(self, job) -> None:
        self._transfer_panel.job_finished(job)
        self._history.record(job)
        # Suppress status-bar noise for intentional skips (up-to-date / skip-existing).
        # Those have job.error set to a reason string; only user-initiated cancellations
        # have no error message.
        if not job.error:
            self._status.showMessage(f"Cancelled: {job.filename}")

    def _on_pause_resume(self) -> None:
        if not self._queue:
            return
        if self._queue.is_paused():
            self._queue.unpause()
            self._transfer_panel.set_paused(False)
            self._status.showMessage("Transfers resumed")
        else:
            self._queue.pause()
            self._transfer_panel.set_paused(True)
            self._status.showMessage("Transfers paused")

    def _on_cancel(self) -> None:
        if self._queue:
            self._queue.cancel_current()

    def _on_resume(self, job) -> None:
        if self._queue:
            self._queue.resume(job)
            self._status.showMessage(f"Resuming: {job.filename}")

    def _on_sync(self) -> None:
        if not self._active_conn or not self._queue:
            return
        dlg = SyncDialog(
            local_dir=self._local_panel.current_path(),
            remote_dir=self._remote_panel.current_path(),
            conn=self._active_conn,
            on_enqueue=self._enqueue_sync_jobs,
            parent=self,
        )
        dlg.exec()

    def _enqueue_sync_jobs(self, jobs: list[TransferJob]) -> None:
        for job in jobs:
            self._signals.job_enqueued.emit(job)
        for job in jobs:
            self._queue.enqueue(job)
        self._signals.status.emit(
            f"Queued {len(jobs)} sync job(s)"
        )

    # ── Keyboard shortcut cheatsheet ──────────────────────────────────────────

    def _on_search(self) -> None:
        """Open the remote search dialog (Ctrl+F)."""
        if self._sftp is None:
            self._status.showMessage("Connect to a server first")
            return
        from sftp_ui.ui.dialogs.search_dialog import SearchDialog
        dlg = SearchDialog(self._sftp, self._remote_panel._cwd, parent=self)
        dlg.navigate_to.connect(self._remote_panel.navigate)
        dlg.show()

    def _show_shortcuts_dialog(self) -> None:
        """Open the keyboard shortcut help overlay (F1 / Ctrl+?)."""
        dlg = ShortcutsDialog(self)
        dlg.exec()

    # ── Command Palette ────────────────────────────────────────────────────────

    def _show_command_palette(self) -> None:
        """Open the command palette (Ctrl+P)."""
        dlg = CommandPaletteDialog(self._command_registry, parent=self)
        dlg.exec()

    def _register_commands(self) -> None:
        """Register all available commands in the command registry."""
        r = self._command_registry
        is_connected = lambda: self._sftp is not None

        # Connection
        r.register(Command(id="conn.toggle", name="Connect / Disconnect", category="Connection",
                           handler=self._toggle_connection, shortcut="Ctrl+K"))
        r.register(Command(id="conn.new", name="New Connection", category="Connection",
                           handler=self._on_new_connection, shortcut="Ctrl+N"))
        r.register(Command(id="conn.manage", name="Manage Connections", category="Connection",
                           handler=self._on_manage_connections))
        r.register(Command(id="conn.bookmarks", name="Toggle Bookmarks Bar", category="Connection",
                           handler=self._toggle_bookmarks_bar, shortcut="Ctrl+B"))

        # Navigation
        r.register(Command(id="nav.refresh", name="Refresh Remote", category="Navigation",
                           handler=self._remote_panel.refresh, shortcut="Ctrl+R",
                           enabled_when=is_connected))
        r.register(Command(id="nav.goto", name="Go to Path", category="Navigation",
                           handler=self._remote_panel.focus_path_input, shortcut="Ctrl+G",
                           enabled_when=is_connected))
        r.register(Command(id="nav.hidden", name="Toggle Hidden Files", category="Navigation",
                           handler=self._remote_panel.toggle_hidden, shortcut="Ctrl+Shift+.",
                           enabled_when=is_connected))

        # Transfer
        r.register(Command(id="transfer.sync", name="Sync Directories", category="Transfer",
                           handler=self._on_sync, enabled_when=is_connected))

        # Search
        r.register(Command(id="search.remote", name="Search Remote Files", category="Search",
                           handler=self._on_search, shortcut="Ctrl+F",
                           enabled_when=is_connected))

        # UI
        r.register(Command(id="ui.shortcuts", name="Keyboard Shortcuts", category="UI",
                           handler=self._show_shortcuts_dialog, shortcut="F1"))
        r.register(Command(id="ui.palette", name="Command Palette", category="UI",
                           handler=self._show_command_palette, shortcut="Ctrl+P"))

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _on_open_theme_dialog(self) -> None:
        """Open the theme picker dialog."""
        if not self._theme_manager:
            return
        from sftp_ui.ui.dialogs.theme_dialog import ThemeDialog
        dlg = ThemeDialog(self._theme_manager, self)
        dlg.exec()

    # ── Geometry persistence ───────────────────────────────────────────────────

    def _restore_geometry(self) -> None:
        """Restore window size and position saved from the previous session."""
        settings = QSettings("sftp-ui", "sftp-ui")
        geometry: QByteArray = settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_geometry(self) -> None:
        """Persist window size and position for the next session."""
        settings = QSettings("sftp-ui", "sftp-ui")
        settings.setValue("window/geometry", self.saveGeometry())

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_geometry()
        # If transfers are in progress _on_disconnect() shows a confirmation
        # dialog.  When the user clicks No, _on_disconnect returns without
        # clearing self._queue, so we can detect that the close was rejected.
        # We must call event.ignore() in that case — otherwise Qt closes the
        # window regardless of the user's answer.
        queue_before = self._queue
        self._on_disconnect()
        if queue_before is not None and self._queue is not None:
            # _on_disconnect returned early (user declined) — block the close.
            event.ignore()
            return
        super().closeEvent(event)
