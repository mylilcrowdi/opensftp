"""
ConnectionComboDelegate — paints a status dot next to each connection name.

Active (connected) sessions get a green dot, idle ones get a grey dot.
The delegate reads item data role Qt.UserRole + 1 for connection state.
"""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

# Custom data role for connection state
ROLE_CONNECTED = Qt.ItemDataRole.UserRole + 1

_DOT_RADIUS = 4
_DOT_MARGIN = 8
_COLOR_CONNECTED = QColor("#a6e3a1")  # Catppuccin green
_COLOR_IDLE = QColor("#585b70")        # Catppuccin surface2


class ConnectionComboDelegate(QStyledItemDelegate):
    """Draws a small colored dot before each combo item to indicate state."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        # Let the base class draw text, selection highlight, etc.
        super().paint(painter, option, index)

        # Draw dot
        is_connected = index.data(ROLE_CONNECTED)
        if is_connected is None:
            return

        color = _COLOR_CONNECTED if is_connected else _COLOR_IDLE
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(color)

        # Position: vertically centered, right-aligned with margin
        rect = option.rect
        cx = rect.right() - _DOT_MARGIN - _DOT_RADIUS
        cy = rect.center().y()
        painter.drawEllipse(cx - _DOT_RADIUS, cy - _DOT_RADIUS,
                            _DOT_RADIUS * 2, _DOT_RADIUS * 2)
        painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        size = super().sizeHint(option, index)
        # Add space for the dot on the right
        return QSize(size.width() + _DOT_MARGIN + _DOT_RADIUS * 2 + 4, max(size.height(), 24))
