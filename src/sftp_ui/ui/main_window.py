"""
MainWindow — composes toolbar, tab bar, and wires up the application.

Each tab holds a SessionWidget that owns its own panels, connection,
transfer queue, and auto-reconnect logic. The toolbar delegates to the
currently active tab.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QByteArray, QSettings, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from sftp_ui.core.connection import Connection, ConnectionStore
from sftp_ui.core.license import LicenseManager, LicenseStatus
from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient
from sftp_ui.core.transfer_history import TransferHistory
from sftp_ui.core.ui_state import UIState
from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
from sftp_ui.ui.dialogs.connection_manager import ConnectionManagerDialog
from sftp_ui.ui.dialogs.command_palette import CommandPaletteDialog
from sftp_ui.ui.dialogs.shortcuts_dialog import ShortcutsDialog
from sftp_ui.core.command_registry import Command, CommandRegistry
from sftp_ui.ui.dialogs.sync_dialog import SyncDialog
from sftp_ui.ui.session_widget import SessionWidget
from sftp_ui.ui.widgets.session_sidebar import SessionSidebar
from sftp_ui.ui.widgets.status_dot import StatusDot
from sftp_ui.ui.widgets.animated_status_bar import AnimatedStatusBar
from sftp_ui.ui.widgets.connection_combo_delegate import (
    ConnectionComboDelegate, ROLE_CONNECTED,
)
from sftp_ui.ui.widgets.bookmarks_bar import BookmarksBar
from sftp_ui.animations.transitions import fade_in
from sftp_ui.ui.glass_frame import GlassBackground, GlassFrame

if TYPE_CHECKING:
    from sftp_ui.styling.theme_manager import ThemeManager


# ── MainWindow ────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    MAX_TABS_FREE = 3
    MAX_TABS_PRO  = 16

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
        self._theme_manager = theme_manager
        self._ui_state = UIState()
        self._license = LicenseManager()

        # Persistent transfer history
        self._history = TransferHistory(Path.home() / ".config" / "sftp-ui" / "transfer_history.jsonl")

        self._command_registry = CommandRegistry()

        self._build_ui()
        self._connect_signals()
        self._register_commands()
        self._reload_connection_list()
        self._restore_geometry()
        self._apply_frost_state()
        if self._theme_manager:
            self._theme_manager.theme_changed.connect(lambda _: self._apply_frost_state())
        QTimer.singleShot(0, self._restore_tabs)

        # Periodic update for tab badges (transfer counts) and combo dots
        self._badge_timer = QTimer(self)
        self._badge_timer.setInterval(2000)
        self._badge_timer.timeout.connect(self._update_tab_badges)
        self._badge_timer.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Glass background layer (painted gradient for Frost theme)
        self._glass_bg = GlassBackground()
        self.setCentralWidget(self._glass_bg)
        root = QVBoxLayout(self._glass_bg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar glass wrapper
        self._glass_toolbar = GlassFrame()
        self._glass_toolbar.layout().addWidget(self._build_toolbar())
        root.addWidget(self._glass_toolbar)

        # Bookmarks bar — auto-hides when no favorites are starred
        self._bookmarks_bar = BookmarksBar(self._store, parent=self._glass_bg)
        self._bookmarks_bar.connect_requested.connect(self._on_bookmark_connect)
        root.addWidget(self._bookmarks_bar)

        # Tab widget — each tab holds a SessionWidget
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self._on_close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # "+" button for new tab
        new_tab_btn = QToolButton()
        new_tab_btn.setText("+")
        new_tab_btn.setToolTip("New tab (Ctrl+T)")
        new_tab_btn.clicked.connect(self._on_new_tab)
        self._tabs.setCornerWidget(new_tab_btn, Qt.Corner.TopRightCorner)

        # Session sidebar (hidden by default, toggle via Ctrl+Shift+S)
        self._sidebar = SessionSidebar()
        self._sidebar.setVisible(False)
        self._sidebar.tab_switch_requested.connect(self._tabs.setCurrentIndex)

        from PySide6.QtWidgets import QHBoxLayout as _HBox
        content_row = QWidget()
        content_layout = _HBox(content_row)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._sidebar)
        content_layout.addWidget(self._tabs, stretch=1)

        root.addWidget(content_row, stretch=1)

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
        self._conn_combo.setMaximumWidth(320)
        self._conn_combo.setPlaceholderText("Select connection…")
        self._conn_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._combo_delegate = ConnectionComboDelegate(self._conn_combo)
        self._conn_combo.setItemDelegate(self._combo_delegate)

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
        self._conn_combo.activated.connect(lambda _idx: self._on_connect())

        return bar

    def _connect_signals(self) -> None:
        self._refresh_btn.clicked.connect(
            lambda: self._delegate_to_remote("refresh")
        )

        # Keyboard shortcuts — panel actions delegate to active session
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(
            lambda: self._delegate_to_remote("refresh")
        )
        QShortcut(QKeySequence("F5"), self).activated.connect(
            lambda: self._delegate_to_remote("refresh")
        )
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._toggle_connection)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._on_new_connection)
        QShortcut(QKeySequence("Ctrl+Shift+."), self).activated.connect(
            lambda: self._delegate_to_remote("toggle_hidden")
        )
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(
            lambda: self._delegate_to_remote("focus_path_input")
        )
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._toggle_bookmarks_bar)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self._toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._on_search)
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self._show_command_palette)
        QShortcut(QKeySequence("F1"), self).activated.connect(self._show_shortcuts_dialog)
        QShortcut(QKeySequence("Ctrl+?"), self).activated.connect(self._show_shortcuts_dialog)

        # Tab shortcuts
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self._on_new_tab)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(
            lambda: self._on_close_tab(self._tabs.currentIndex())
        )
        QShortcut(QKeySequence("Ctrl+Tab"), self).activated.connect(self._next_tab)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self).activated.connect(self._prev_tab)

    def _delegate_to_remote(self, method_name: str) -> None:
        """Call a method on the active session's remote panel."""
        session = self._active_session()
        if session:
            method = getattr(session.remote_panel, method_name, None)
            if method:
                method()

    # ── Active session helper ─────────────────────────────────────────────────

    def _active_session(self) -> Optional[SessionWidget]:
        """Return the currently visible SessionWidget, or None."""
        w = self._tabs.currentWidget()
        return w if isinstance(w, SessionWidget) else None

    # ── Tab management ─────────────────────────────────────────────────────────

    @property
    def _max_tabs(self) -> int:
        if self._license.status() == LicenseStatus.PRO:
            return self.MAX_TABS_PRO
        return self.MAX_TABS_FREE

    def _add_new_tab(self, conn: Optional[Connection] = None) -> SessionWidget:
        """Create a new SessionWidget tab. Optionally connect to a server."""
        limit = self._max_tabs
        if self._tabs.count() >= limit:
            if self._license.status() != LicenseStatus.PRO:
                self._show_tab_limit_gate()
            else:
                QMessageBox.warning(self, "Tab limit", f"Maximum {limit} tabs.")
            s = self._active_session()
            if s:
                return s
            return self._tabs.widget(0)

        session = SessionWidget(ui_state=self._ui_state, parent=self._tabs)
        session.set_frost_active(self._is_frost())

        # Wire session signals to MainWindow
        session.connection_changed.connect(
            lambda c, s=session: self._on_session_connection_changed(s, c)
        )
        session.status_message.connect(self._status.showMessage)
        session.job_finished.connect(self._history.record)
        session.reconnect_state_changed.connect(self._update_tab_badges)
        session.cross_session_transfer.connect(
            lambda src_id, entries, dest, s=session: self._on_cross_session_transfer(s, src_id, entries, dest)
        )

        # Persist navigation state
        session.local_panel.path_changed.connect(self._ui_state.set_local_path)
        session.remote_panel.path_changed.connect(
            lambda path, s=session: self._on_remote_path_changed(s, path)
        )

        # Persist sort and column state
        session.remote_panel.column_widths_changed.connect(
            lambda widths: self._ui_state.set_column_widths("remote", widths)
        )
        session.remote_panel.sort_state_changed.connect(
            lambda col, order: self._ui_state.set_sort_state("remote", col, order)
        )
        session.local_panel.sort_state_changed.connect(
            lambda col, order: self._ui_state.set_sort_state("local", col, order)
        )

        # Restore local panel sort
        local_sort_col, local_sort_order = self._ui_state.get_sort_state("local")
        if local_sort_col != -1:
            session.local_panel.restore_sort_state(local_sort_col, local_sort_order)

        label = "New Tab"
        idx = self._tabs.addTab(session, label)
        self._tabs.setCurrentIndex(idx)

        if conn:
            session.connect_to(conn)

        return session

    def _on_new_tab(self) -> None:
        self._add_new_tab()

    def _on_close_tab(self, index: int) -> None:
        if index < 0 or index >= self._tabs.count():
            return
        session = self._tabs.widget(index)
        if not isinstance(session, SessionWidget):
            return
        if session.is_connected and not session.disconnect():
            return  # user cancelled
        self._tabs.removeTab(index)
        session.deleteLater()
        if self._tabs.count() == 0:
            self._add_new_tab()

    def _on_tab_changed(self, index: int) -> None:
        """Sync toolbar state with the newly active tab."""
        session = self._tabs.widget(index)
        if not isinstance(session, SessionWidget):
            return
        self._sync_toolbar_to_session(session)
        if self._sidebar.isVisible():
            self._sidebar.set_active_index(index)

    def _next_tab(self) -> None:
        if self._tabs.count() <= 1:
            return
        idx = (self._tabs.currentIndex() + 1) % self._tabs.count()
        self._tabs.setCurrentIndex(idx)

    def _prev_tab(self) -> None:
        if self._tabs.count() <= 1:
            return
        idx = (self._tabs.currentIndex() - 1) % self._tabs.count()
        self._tabs.setCurrentIndex(idx)

    # ── Toolbar sync ──────────────────────────────────────────────────────────

    def _sync_toolbar_to_session(self, session: SessionWidget) -> None:
        """Update all toolbar widgets to reflect the given session's state."""
        connected = session.is_connected
        conn = session.active_conn

        self._connect_btn.setEnabled(not connected)
        self._disconnect_btn.setEnabled(connected)
        self._refresh_btn.setEnabled(connected)
        self._sync_btn.setEnabled(connected)

        if connected and conn:
            self._status_dot.set_connected()
            self.setWindowTitle(f"SFTP UI — {conn.name}")
            for i in range(self._conn_combo.count()):
                if self._conn_combo.itemData(i) == conn.id:
                    self._conn_combo.setCurrentIndex(i)
                    break
        else:
            self._status_dot.set_idle()
            self.setWindowTitle("SFTP UI")

    def _on_session_connection_changed(self, session: SessionWidget, conn: Optional[Connection]) -> None:
        """Handle connection state change from any SessionWidget."""
        idx = self._tabs.indexOf(session)
        if idx >= 0:
            if conn:
                self._tabs.setTabText(idx, conn.name)
            else:
                self._tabs.setTabText(idx, "New Tab")

        # If this is the active tab, sync toolbar
        if session == self._active_session():
            self._sync_toolbar_to_session(session)

        # Update combo dots immediately
        self._update_combo_dots()

        # Record connection timestamp
        if conn:
            try:
                self._store.record_connected(conn.id)
            except Exception:
                pass

            # Restore column widths and sort state
            saved_widths = self._ui_state.get_column_widths("remote")
            if saved_widths:
                session.remote_panel.set_column_widths(saved_widths)
            remote_sort_col, remote_sort_order = self._ui_state.get_sort_state("remote")
            session.remote_panel.restore_sort_state(remote_sort_col, remote_sort_order)

    # ── Tab badges & combo dots ──────────────────────────────────────────────

    def _update_tab_badges(self) -> None:
        """Update tab labels with transfer counts and combo dots for active sessions."""
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if not isinstance(session, SessionWidget):
                continue

            conn = session.active_conn
            base = conn.name if conn else "New Tab"

            # Transfer badge
            pending = 0
            if session._queue:
                pending = session._queue.pending_count()

            # Reconnecting indicator
            if session._reconnecting:
                self._tabs.setTabText(i, f"⟳ {base}")
            elif pending > 0:
                self._tabs.setTabText(i, f"{base} ({pending})")
            else:
                self._tabs.setTabText(i, base)

        self._update_combo_dots()
        if self._sidebar.isVisible():
            self._refresh_sidebar()

    def _refresh_sidebar(self) -> None:
        """Sync the sidebar with current tab state."""
        sessions = []
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if not isinstance(session, SessionWidget):
                continue
            conn = session.active_conn
            name = conn.name if conn else "New Tab"
            pending = session._queue.pending_count() if session._queue else 0
            sessions.append((i, name, session.is_connected, session._reconnecting, pending))
        self._sidebar.rebuild(sessions)
        self._sidebar.set_active_index(self._tabs.currentIndex())

    def _update_combo_dots(self) -> None:
        """Set the ROLE_CONNECTED data on each combo item based on active sessions."""
        # Build set of connected connection IDs across all tabs
        connected_ids: set[str] = set()
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if isinstance(session, SessionWidget) and session.is_connected:
                conn = session.active_conn
                if conn:
                    connected_ids.add(conn.id)

        model = self._conn_combo.model()
        for i in range(self._conn_combo.count()):
            conn_id = self._conn_combo.itemData(i)
            is_connected = conn_id in connected_ids
            model.setData(model.index(i, 0), is_connected, ROLE_CONNECTED)

    # ── Connection ────────────────────────────────────────────────────────────

    def _reload_connection_list(self) -> None:
        self._conn_combo.clear()
        conns = self._store.all()
        favorites = sorted([c for c in conns if c.favorite],     key=lambda c: c.name.lower())
        others    = sorted([c for c in conns if not c.favorite], key=lambda c: (c.group.lower(), c.name.lower()))
        for conn in favorites + others:
            label = f"★ {conn.name}" if conn.favorite else conn.name
            if conn.group:
                label += f"  [{conn.group}]"
            self._conn_combo.addItem(label, conn.id)
        if hasattr(self, "_bookmarks_bar"):
            self._bookmarks_bar.refresh()
        if hasattr(self, "_badge_timer"):
            self._update_combo_dots()

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
        session = self._active_session()
        if session and session.is_connected:
            self._on_disconnect()
        else:
            self._on_connect()

    def _on_connect(self) -> None:
        conn = self._selected_connection()
        if not conn:
            QMessageBox.warning(self, "No connection", "Please select or create a connection.")
            return

        session = self._active_session()
        if session and session.is_connected:
            # Active tab already connected — open new tab
            session = self._add_new_tab()
        elif session is None:
            session = self._add_new_tab()

        self._status.showMessage(f"Connecting to {conn.host}…")
        self._connect_btn.setEnabled(False)
        self._status_dot.set_connecting()
        self._ui_state.set_last_connection(conn.id)
        session.connect_to(conn)

    def _on_disconnect(self) -> None:
        session = self._active_session()
        if session:
            session.disconnect()

    def _on_remote_path_changed(self, session: SessionWidget, path: str) -> None:
        conn = session.active_conn
        if conn:
            self._ui_state.set_remote_path(conn.id, path)

    # ── Cross-session transfer ──────────────────────────────────────────────

    def _find_session_by_id(self, session_id: str) -> Optional[SessionWidget]:
        """Find a SessionWidget by its Python object id."""
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if isinstance(session, SessionWidget) and str(id(session)) == session_id:
                return session
        return None

    def _on_cross_session_transfer(
        self,
        dest_session: SessionWidget,
        source_session_id: str,
        entry_dicts: list,
        dest_dir: str,
    ) -> None:
        """Download from source session's server, upload to dest session's server."""
        source_session = self._find_session_by_id(source_session_id)
        if source_session is None or not source_session.is_connected:
            self._status.showMessage("Source session no longer connected.")
            return
        if not dest_session.is_connected:
            self._status.showMessage("Destination session not connected.")
            return

        import os
        import shutil
        import tempfile
        import threading
        from pathlib import PurePosixPath

        src_conn = source_session.active_conn
        entries = [
            RemoteEntry(
                name=d["name"], path=d["path"],
                is_dir=d["is_dir"], size=d.get("size", 0), mtime=0,
            )
            for d in entry_dicts
        ]

        n = sum(1 for _ in entries)
        src_name = src_conn.name if src_conn else "?"
        dst_name = dest_session.active_conn.name if dest_session.active_conn else "?"
        self._status.showMessage(
            f"Cross-session transfer: {n} item(s) from {src_name} → {dst_name}…"
        )

        def _run():
            tmp_dir = tempfile.mkdtemp(prefix="sftp-xfer-")
            try:
                dl_client = SFTPClient()
                dl_client.connect(src_conn)
            except Exception as exc:
                self._status.showMessage(f"Cross-session transfer failed (source connect): {exc}")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            try:
                local_paths: list[str] = []
                for entry in entries:
                    if not entry.is_dir:
                        local_path = os.path.join(tmp_dir, entry.name)
                        try:
                            dl_client.download(entry.path, local_path)
                            local_paths.append(local_path)
                        except Exception as exc:
                            self._status.showMessage(f"Failed to download {entry.name}: {exc}")
                    else:
                        try:
                            remote_files = dl_client.walk(entry.path)
                        except Exception as exc:
                            self._status.showMessage(f"Cannot list {entry.name}: {exc}")
                            continue
                        base = entry.name
                        base_dir = os.path.join(tmp_dir, base)
                        for f in remote_files:
                            rel = PurePosixPath(f.path).relative_to(entry.path)
                            local_path = os.path.join(base_dir, str(rel))
                            os.makedirs(os.path.dirname(local_path), exist_ok=True)
                            try:
                                dl_client.download(f.path, local_path)
                            except Exception:
                                pass
                        if os.path.isdir(base_dir):
                            local_paths.append(base_dir)

                if local_paths:
                    # Post upload to main thread via signal
                    dest_session._signals.cross_upload.emit(local_paths, dest_dir)
                else:
                    self._status.showMessage("Cross-session transfer: no files downloaded.")
            finally:
                dl_client.close()
                # Clean up after a delay to let the upload read the files
                def _cleanup():
                    import time
                    time.sleep(60)
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                threading.Thread(target=_cleanup, daemon=True).start()

        threading.Thread(target=_run, daemon=True, name="cross-session-xfer").start()

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
        if self._bookmarks_bar.isVisible():
            self._bookmarks_bar.setVisible(False)
        else:
            self._bookmarks_bar.refresh()

    def _toggle_sidebar(self) -> None:
        """Toggle the session sidebar visibility."""
        if self._sidebar.isVisible():
            self._sidebar.setVisible(False)
        else:
            self._refresh_sidebar()
            self._sidebar.setVisible(True)

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

    # ── Sync ──────────────────────────────────────────────────────────────────

    def _on_sync(self) -> None:
        session = self._active_session()
        if not session or not session.is_connected or not session._queue:
            return
        dlg = SyncDialog(
            local_dir=session.local_panel.current_path(),
            remote_dir=session.remote_panel.current_path(),
            conn=session.active_conn,
            on_enqueue=session.enqueue_sync_jobs,
            parent=self,
        )
        dlg.exec()

    # ── Search ────────────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        session = self._active_session()
        if not session or not session.is_connected:
            self._status.showMessage("Connect to a server first")
            return
        from sftp_ui.ui.dialogs.search_dialog import SearchDialog
        dlg = SearchDialog(session._sftp, session.remote_panel._cwd, parent=self)
        dlg.navigate_to.connect(session.remote_panel.navigate)
        dlg.show()

    # ── Keyboard shortcut cheatsheet ──────────────────────────────────────────

    def _show_shortcuts_dialog(self) -> None:
        dlg = ShortcutsDialog(self)
        dlg.exec()

    # ── Command Palette ────────────────────────────────────────────────────────

    def _show_command_palette(self) -> None:
        dlg = CommandPaletteDialog(self._command_registry, parent=self)
        dlg.exec()

    def _register_commands(self) -> None:
        r = self._command_registry
        is_connected = lambda: (s := self._active_session()) is not None and s.is_connected

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
                           handler=lambda: self._delegate_to_remote("refresh"),
                           shortcut="Ctrl+R", enabled_when=is_connected))
        r.register(Command(id="nav.goto", name="Go to Path", category="Navigation",
                           handler=lambda: self._delegate_to_remote("focus_path_input"),
                           shortcut="Ctrl+G", enabled_when=is_connected))
        r.register(Command(id="nav.hidden", name="Toggle Hidden Files", category="Navigation",
                           handler=lambda: self._delegate_to_remote("toggle_hidden"),
                           shortcut="Ctrl+Shift+.", enabled_when=is_connected))

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

        # Tabs
        r.register(Command(id="tab.new", name="New Tab", category="Tabs",
                           handler=self._on_new_tab, shortcut="Ctrl+T"))
        r.register(Command(id="tab.close", name="Close Tab", category="Tabs",
                           handler=lambda: self._on_close_tab(self._tabs.currentIndex()),
                           shortcut="Ctrl+W"))
        r.register(Command(id="tab.next", name="Next Tab", category="Tabs",
                           handler=self._next_tab, shortcut="Ctrl+Tab"))
        r.register(Command(id="tab.prev", name="Previous Tab", category="Tabs",
                           handler=self._prev_tab, shortcut="Ctrl+Shift+Tab"))
        r.register(Command(id="tab.sidebar", name="Toggle Session Sidebar", category="Tabs",
                           handler=self._toggle_sidebar, shortcut="Ctrl+Shift+S"))

    # ── Pro gate ──────────────────────────────────────────────────────────

    def _show_tab_limit_gate(self) -> None:
        """Show upgrade prompt when free user hits the tab limit."""
        from sftp_ui.ui.dialogs.license_dialog import LicenseDialog

        msg = QMessageBox(self)
        msg.setWindowTitle("Tab Limit Reached")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"<b>Free version is limited to {self.MAX_TABS_FREE} tabs.</b>"
        )
        msg.setInformativeText(
            f"Upgrade to openSFTP Pro for up to {self.MAX_TABS_PRO} simultaneous sessions."
        )
        upgrade_btn = msg.addButton("Upgrade to Pro", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("OK", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        if msg.clickedButton() is upgrade_btn:
            dlg = LicenseDialog(self._license, parent=self)
            dlg.exec()

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _is_frost(self) -> bool:
        return self._theme_manager is not None and self._theme_manager.current == "frost"

    def _apply_frost_state(self) -> None:
        """Activate or deactivate glass frame effects based on current theme."""
        is_frost = self._is_frost()
        self._glass_bg.set_frost_active(is_frost)
        self._glass_toolbar.set_frost_active(is_frost)
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if isinstance(session, SessionWidget):
                session.set_frost_active(is_frost)

    def _on_open_theme_dialog(self) -> None:
        if not self._theme_manager:
            return
        from sftp_ui.ui.dialogs.theme_dialog import ThemeDialog
        dlg = ThemeDialog(self._theme_manager, self)
        dlg.exec()

    # ── Tab persistence ───────────────────────────────────────────────────────

    def _save_tab_state(self) -> None:
        """Save all open tabs to UIState for restoration on next startup."""
        tabs = []
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if not isinstance(session, SessionWidget):
                continue
            conn = session.active_conn
            tab_info = {
                "connection_id": conn.id if conn else None,
                "local_path": session.local_panel.current_path(),
            }
            tabs.append(tab_info)
        self._ui_state.set_open_tabs(tabs)
        self._ui_state.set_active_tab_index(self._tabs.currentIndex())
        # Legacy compat: set was_connected based on whether any tab is connected
        any_connected = any(
            isinstance(self._tabs.widget(i), SessionWidget) and self._tabs.widget(i).is_connected
            for i in range(self._tabs.count())
        )
        self._ui_state.set_was_connected(any_connected)

    def _restore_tabs(self) -> None:
        """Restore tabs from previous session, or create a single empty tab."""
        tabs = self._ui_state.open_tabs
        if not tabs:
            # Legacy: try single-connection restore
            if self._ui_state.was_connected and self._ui_state.last_connection_id:
                try:
                    conn = self._store.get(self._ui_state.last_connection_id)
                    self._add_new_tab(conn)
                    return
                except KeyError:
                    pass
            self._add_new_tab()
            return

        for tab_info in tabs:
            conn_id = tab_info.get("connection_id")
            conn = None
            if conn_id:
                try:
                    conn = self._store.get(conn_id)
                except KeyError:
                    pass
            self._add_new_tab(conn)

        idx = self._ui_state.active_tab_index
        if 0 <= idx < self._tabs.count():
            self._tabs.setCurrentIndex(idx)

    # ── Geometry persistence ───────────────────────────────────────────────────

    def _restore_geometry(self) -> None:
        settings = QSettings("sftp-ui", "sftp-ui")
        geometry: QByteArray = settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_geometry(self) -> None:
        settings = QSettings("sftp-ui", "sftp-ui")
        settings.setValue("window/geometry", self.saveGeometry())

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_geometry()
        self._save_tab_state()
        # Disconnect all tabs — if any declines, block the close
        for i in range(self._tabs.count()):
            session = self._tabs.widget(i)
            if isinstance(session, SessionWidget) and session.is_connected:
                if not session.disconnect():
                    event.ignore()
                    return
        super().closeEvent(event)
