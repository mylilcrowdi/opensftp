"""
Connection Manager dialog — browse, organize and connect to saved connections.

Features:
  • Favorites pinned to the top
  • Group-based sections
  • Last-connected timestamp
  • In-dialog quick connect / edit / delete
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from sftp_ui.core.connection import Connection, ConnectionStore


def _time_ago(ts: float) -> str:
    """Return a human-readable 'X ago' string, or 'Never'."""
    if ts == 0:
        return "Never"
    diff = time.time() - ts
    if diff < 60:
        return "Just now"
    if diff < 3600:
        m = int(diff / 60)
        return f"{m}m ago"
    if diff < 86400:
        h = int(diff / 3600)
        return f"{h}h ago"
    d = int(diff / 86400)
    return f"{d}d ago"


class _ConnItem(QWidget):
    """Custom row widget for a single connection entry."""

    def __init__(self, conn: Connection, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        # Star for favorites
        self._star = QLabel("★" if conn.favorite else "☆")
        self._star.setFixedWidth(16)
        self._star.setStyleSheet(
            "color: #f9e2af; font-size: 14px;" if conn.favorite
            else "color: #45475a; font-size: 14px;"
        )
        layout.addWidget(self._star)

        # Name + host
        info = QVBoxLayout()
        info.setSpacing(1)

        name_lbl = QLabel(conn.name)
        f = QFont(name_lbl.font())
        f.setBold(True)
        name_lbl.setFont(f)

        host_lbl = QLabel(f"{conn.user}@{conn.host}:{conn.port}")
        host_lbl.setStyleSheet("color: #6c7086; font-size: 11px;")

        info.addWidget(name_lbl)
        info.addWidget(host_lbl)
        layout.addLayout(info, stretch=1)

        # Last connected
        last_lbl = QLabel(_time_ago(conn.last_connected))
        last_lbl.setStyleSheet("color: #585b70; font-size: 11px;")
        last_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(last_lbl)

    def refresh(self, conn: Connection) -> None:
        self.conn = conn
        self._star.setText("★" if conn.favorite else "☆")
        self._star.setStyleSheet(
            "color: #f9e2af; font-size: 14px;" if conn.favorite
            else "color: #45475a; font-size: 14px;"
        )


class _SectionItem(QWidget):
    """Non-selectable group header row."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        lbl = QLabel(label.upper())
        lbl.setStyleSheet("color: #585b70; font-size: 10px; font-weight: 600; letter-spacing: 1px;")
        layout.addWidget(lbl)
        layout.addStretch()


class ConnectionManagerDialog(QDialog):
    """Full connection manager with favorites, groups and last-connected info."""

    # Emitted when the user clicks Connect — parent should initiate the connection.
    connect_requested = Signal(object)   # Connection

    def __init__(
        self,
        store: ConnectionStore,
        on_connect: Optional[Callable[[Connection], None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._on_connect_cb = on_connect
        self.setWindowTitle("Connection Manager")
        self.setMinimumSize(500, 380)
        self._build_ui()
        self._populate()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # List
        self._list = QListWidget()
        self._list.setSpacing(1)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.installEventFilter(self)
        layout.addWidget(self._list, stretch=1)

        # Action buttons row
        btn_row = QHBoxLayout()

        self._new_btn    = QPushButton("+ New")
        self._edit_btn   = QPushButton("✎ Edit")
        self._fav_btn    = QPushButton("★ Favorite")
        self._del_btn    = QPushButton("✕ Delete")
        self._del_btn.setObjectName("danger")

        self._connect_btn = QPushButton("→ Connect")
        self._connect_btn.setObjectName("primary")

        for b in (self._new_btn, self._edit_btn, self._fav_btn, self._del_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        btn_row.addWidget(self._connect_btn)

        layout.addLayout(btn_row)

        # Close button
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        # Wire up
        self._new_btn.clicked.connect(self._on_new)
        self._edit_btn.clicked.connect(self._on_edit)
        self._fav_btn.clicked.connect(self._on_toggle_favorite)
        self._del_btn.clicked.connect(self._on_delete)
        self._connect_btn.clicked.connect(self._on_connect)

        self._on_selection_changed()  # initial button state

    def _populate(self) -> None:
        self._list.clear()
        conns = self._store.all()

        # Sort: favorites first, then by group, then by name
        favorites = sorted([c for c in conns if c.favorite], key=lambda c: c.name.lower())
        others    = sorted([c for c in conns if not c.favorite], key=lambda c: (c.group.lower(), c.name.lower()))

        if favorites:
            self._add_section("⭐  Favorites")
            for c in favorites:
                self._add_conn_item(c)

        # Group non-favorites
        current_group = None
        for c in others:
            g = c.group or ""
            if g != current_group:
                current_group = g
                self._add_section(f"🗂  {g}" if g else "Other")
            self._add_conn_item(c)

        self._on_selection_changed()

    def _add_section(self, label: str) -> None:
        item = QListWidgetItem(self._list)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        widget = _SectionItem(label)
        item.setSizeHint(widget.sizeHint())
        self._list.setItemWidget(item, widget)

    def _add_conn_item(self, conn: Connection) -> None:
        item = QListWidgetItem(self._list)
        item.setData(Qt.ItemDataRole.UserRole, conn.id)
        widget = _ConnItem(conn)
        item.setSizeHint(widget.sizeHint())
        self._list.setItemWidget(item, widget)

    # ── Event filter ──────────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        """Allow Enter/Return to trigger Connect when the list has focus."""
        if obj is self._list and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._on_connect()
                return True
        return super().eventFilter(obj, event)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _selected_conn(self) -> Optional[Connection]:
        items = self._list.selectedItems()
        if not items:
            return None
        conn_id = items[0].data(Qt.ItemDataRole.UserRole)
        if not conn_id:
            return None
        try:
            return self._store.get(conn_id)
        except KeyError:
            return None

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        has_sel = self._selected_conn() is not None
        self._edit_btn.setEnabled(has_sel)
        self._fav_btn.setEnabled(has_sel)
        self._del_btn.setEnabled(has_sel)
        self._connect_btn.setEnabled(has_sel)
        if has_sel:
            c = self._selected_conn()
            self._fav_btn.setText("☆ Unfavorite" if c.favorite else "★ Favorite")

    def _on_double_click(self, item: QListWidgetItem) -> None:
        if item.data(Qt.ItemDataRole.UserRole):
            self._on_connect()

    def _on_new(self) -> None:
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog(self)
        if dlg.exec():
            self._store.add(dlg.result_connection())
            self._populate()

    def _on_edit(self) -> None:
        conn = self._selected_conn()
        if not conn:
            return
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog(self, conn=conn)
        if dlg.exec():
            self._store.update(dlg.result_connection())
            self._populate()

    def _on_toggle_favorite(self) -> None:
        import dataclasses
        conn = self._selected_conn()
        if not conn:
            return
        updated = dataclasses.replace(conn, favorite=not conn.favorite)
        self._store.update(updated)
        self._populate()

    def _on_delete(self) -> None:
        conn = self._selected_conn()
        if not conn:
            return
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(
            self, "Delete", f"Delete '{conn.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self._store.remove(conn.id)
            self._populate()

    def _on_connect(self) -> None:
        conn = self._selected_conn()
        if not conn:
            return
        self.connect_requested.emit(conn)
        if self._on_connect_cb:
            self._on_connect_cb(conn)
        self.accept()
