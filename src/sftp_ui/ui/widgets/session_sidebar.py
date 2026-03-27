"""
SessionSidebar — compact vertical panel listing all open sessions.

Each session is a clickable card showing:
- Connection name (or "New Tab")
- Colored status dot (connected/disconnected/reconnecting)
- Transfer count badge

Toggle via Ctrl+Shift+S or command palette.
Clicking a card switches to that tab.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from sftp_ui.ui.session_widget import SessionWidget


_DOT_COLORS = {
    "connected":    "#a6e3a1",
    "disconnected": "#585b70",
    "reconnecting": "#f9e2af",
}


class _SessionCard(QFrame):
    """One row in the sidebar representing a single session/tab."""

    clicked = Signal(int)  # tab index

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self._index = index
        self._is_active = False
        self._status_color = QColor(_DOT_COLORS["disconnected"])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # Status dot
        self._dot = QWidget()
        self._dot.setFixedSize(8, 8)
        layout.addWidget(self._dot)

        # Name label
        self._name_label = QLabel("New Tab")
        self._name_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._name_label, stretch=1)

        # Badge label (transfer count)
        self._badge = QLabel("")
        self._badge.setStyleSheet(
            "font-size: 10px; color: #7f849c; padding: 0 4px;"
        )
        self._badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._badge)

        self._update_style()

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self._update_style()

    def set_name(self, name: str) -> None:
        self._name_label.setText(name)

    def set_status(self, status: str) -> None:
        self._status_color = QColor(_DOT_COLORS.get(status, _DOT_COLORS["disconnected"]))
        self._dot.update()
        self.update()

    def set_badge(self, count: int) -> None:
        if count > 0:
            self._badge.setText(f"({count})")
        else:
            self._badge.setText("")

    def _update_style(self) -> None:
        if self._is_active:
            self.setStyleSheet(
                "_SessionCard { background: rgba(137, 180, 250, 0.12); "
                "border-radius: 6px; border: 1px solid rgba(137, 180, 250, 0.25); }"
            )
        else:
            self.setStyleSheet(
                "_SessionCard { background: transparent; "
                "border-radius: 6px; border: 1px solid transparent; }"
                "_SessionCard:hover { background: rgba(255, 255, 255, 0.04); "
                "border: 1px solid rgba(255, 255, 255, 0.08); }"
            )

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._index)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # Draw the status dot
        p = QPainter(self._dot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._status_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 6, 6)
        p.end()


class SessionSidebar(QWidget):
    """Vertical sidebar listing all open sessions."""

    tab_switch_requested = Signal(int)  # index of tab to switch to

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(180)
        self.setObjectName("session_sidebar")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 8, 4, 8)
        outer.setSpacing(0)

        header = QLabel("Sessions")
        header.setStyleSheet("font-size: 11px; font-weight: bold; color: #7f849c; padding: 0 8px 6px;")
        outer.addWidget(header)

        # Scroll area for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.addStretch()

        scroll.setWidget(self._container)
        outer.addWidget(scroll, stretch=1)

        self._cards: list[_SessionCard] = []

    def rebuild(self, sessions: list[tuple[int, str, bool, bool, int]]) -> None:
        """Rebuild cards from tab data.

        Args:
            sessions: List of (tab_index, name, is_connected, is_reconnecting, pending_transfers)
        """
        # Clear old cards
        for card in self._cards:
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for tab_idx, name, connected, reconnecting, pending in sessions:
            card = _SessionCard(tab_idx, self._container)
            card.set_name(name)
            if reconnecting:
                card.set_status("reconnecting")
            elif connected:
                card.set_status("connected")
            else:
                card.set_status("disconnected")
            card.set_badge(pending)
            card.clicked.connect(self.tab_switch_requested.emit)
            self._layout.insertWidget(self._layout.count() - 1, card)  # before stretch
            self._cards.append(card)

    def set_active_index(self, tab_index: int) -> None:
        """Highlight the currently active tab's card."""
        for card in self._cards:
            card.set_active(card._index == tab_index)

    def update_card(self, tab_index: int, name: str, connected: bool,
                    reconnecting: bool, pending: int) -> None:
        """Update a single card without full rebuild."""
        for card in self._cards:
            if card._index == tab_index:
                card.set_name(name)
                if reconnecting:
                    card.set_status("reconnecting")
                elif connected:
                    card.set_status("connected")
                else:
                    card.set_status("disconnected")
                card.set_badge(pending)
                break
