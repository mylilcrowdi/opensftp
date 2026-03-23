"""
BookmarksBar — horizontal quick-connect bar for favorited connections.

Shows each starred connection as a clickable chip directly below the toolbar.
Clicking a chip emits ``connect_requested(Connection)`` so the caller can
initiate the connection without opening any dialog.

Usage::

    bar = BookmarksBar(store)
    bar.connect_requested.connect(my_connect_handler)
    bar.refresh()

The bar auto-hides when there are no favorites (so it takes no vertical space
when unused).
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from sftp_ui.core.connection import Connection, ConnectionStore


class _ChipButton(QPushButton):
    """A compact pill-shaped button representing a single bookmarked connection."""

    def __init__(self, conn: Connection, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.conn = conn
        self._build(conn)

    def _build(self, conn: Connection) -> None:
        self.setText(f"★ {conn.name}")
        self.setToolTip(f"{conn.user}@{conn.host}:{conn.port}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("bookmarkChip")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def update_conn(self, conn: Connection) -> None:
        """Refresh displayed data without recreating the widget."""
        self.conn = conn
        self._build(conn)


class BookmarksBar(QWidget):
    """
    Horizontal strip of chip buttons for favorited connections.

    Signals
    -------
    connect_requested : Signal(Connection)
        Emitted when the user clicks a chip.
    visibility_changed : Signal(bool)
        Emitted after the bar shows or hides itself (follows favorite count).
    """

    connect_requested: Signal = Signal(object)   # Connection
    visibility_changed: Signal = Signal(bool)

    def __init__(
        self,
        store: ConnectionStore,
        on_connect: Optional[Callable[[Connection], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._on_connect_cb = on_connect
        self._chips: dict[str, _ChipButton] = {}   # conn.id → chip
        self._build_ui()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 0, 12, 0)
        outer.setSpacing(0)

        # "Bookmarks:" label
        self._label = QLabel("Bookmarks:")
        lbl_font = QFont(self._label.font())
        lbl_font.setPointSize(max(lbl_font.pointSize() - 1, 8))
        self._label.setFont(lbl_font)
        self._label.setStyleSheet("color: #585b70; padding-right: 6px;")
        outer.addWidget(self._label)

        # Scrollable chip area
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(True)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._scroll.setFixedHeight(34)

        self._chip_container = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_container)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(6)
        self._chip_layout.addStretch()
        self._scroll.setWidget(self._chip_container)

        outer.addWidget(self._scroll, stretch=1)

        # Fixed height for the whole bar
        self.setFixedHeight(38)
        self.setObjectName("bookmarksBar")

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-read the store and rebuild chip buttons to match current favorites."""
        favorites = sorted(
            [c for c in self._store.all() if c.favorite],
            key=lambda c: c.name.lower(),
        )

        # Determine which ids to add, update, or remove
        current_ids = set(self._chips.keys())
        new_ids = {c.id for c in favorites}

        # Remove chips for connections that are no longer favorites.
        # setParent(None) immediately detaches from the parent so that
        # findChildren() no longer sees the widget (unlike deleteLater alone).
        for conn_id in current_ids - new_ids:
            chip = self._chips.pop(conn_id)
            self._chip_layout.removeWidget(chip)
            chip.setParent(None)   # detach immediately
            chip.deleteLater()     # then schedule C++ object cleanup

        # Add chips for new favorites (inserted before the trailing stretch)
        stretch_idx = self._chip_layout.count() - 1  # last item is the stretch
        for conn in favorites:
            if conn.id not in self._chips:
                chip = _ChipButton(conn, self._chip_container)
                chip.clicked.connect(self._make_connect_slot(conn.id))
                self._chips[conn.id] = chip
                self._chip_layout.insertWidget(stretch_idx, chip)
                stretch_idx += 1
            else:
                # Update label / tooltip in case the connection was renamed
                self._chips[conn.id].update_conn(conn)

        # Show/hide the entire bar depending on whether there are any favorites
        was_visible = self.isVisible()
        should_show = bool(favorites)
        self.setVisible(should_show)
        if was_visible != should_show:
            self.visibility_changed.emit(should_show)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _make_connect_slot(self, conn_id: str) -> Callable[[], None]:
        """Return a zero-argument slot that fires connect_requested for conn_id."""

        def _slot() -> None:
            try:
                conn = self._store.get(conn_id)
            except KeyError:
                return
            self.connect_requested.emit(conn)
            if self._on_connect_cb:
                self._on_connect_cb(conn)

        return _slot
