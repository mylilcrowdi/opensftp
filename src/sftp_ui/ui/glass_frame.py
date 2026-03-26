"""
GlassFrame — semi-transparent panel wrapper for the Frost theme.

Paints a rounded rectangle with rgba fill and a subtle luminous top-edge,
giving the impression of a frosted glass panel floating on a dark surface.
Only activates when the current theme is "frost"; otherwise fully transparent.

Cross-platform: uses pure QPainter, no OS-specific APIs, no window flags.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget


class GlassFrame(QWidget):
    """Container that paints a glass panel effect behind its children.

    Usage::

        frame = GlassFrame(theme_manager)
        frame.layout().addWidget(child_widget)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._radius = 10.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def set_frost_active(self, active: bool) -> None:
        """Enable or disable the glass paint effect."""
        if self._active != active:
            self._active = active
            self.update()

    def paintEvent(self, event) -> None:
        if not self._active:
            return super().paintEvent(event)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = self._radius

        # Glass fill: semi-transparent dark blue
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        fill = QColor(14, 16, 32, 190)  # rgba(14, 16, 32, 0.75)
        p.fillPath(path, fill)

        # Top edge highlight: subtle white glow fading down
        highlight_rect = QRectF(rect.x(), rect.y(), rect.width(), 1.5)
        highlight = QLinearGradient(highlight_rect.left(), 0, highlight_rect.right(), 0)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 0))
        highlight.setColorAt(0.3, QColor(255, 255, 255, 18))
        highlight.setColorAt(0.7, QColor(255, 255, 255, 18))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))

        p.setPen(Qt.PenStyle.NoPen)
        top_path = QPainterPath()
        top_path.addRoundedRect(highlight_rect, r, r)
        p.fillPath(top_path, highlight)

        # Border: very subtle bright edge
        border_color = QColor(255, 255, 255, 12)
        p.setPen(QPen(border_color, 0.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, r, r)

        # Bottom inner shadow line for depth
        shadow_rect = QRectF(rect.x() + 1, rect.bottom() - 1.5, rect.width() - 2, 1)
        shadow = QColor(0, 0, 0, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(shadow_rect, shadow)

        p.end()


class GlassBackground(QWidget):
    """Widget that paints a subtle gradient background for the Frost theme.

    Place as the central widget's background layer. Only paints when active.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_frost_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def paintEvent(self, event) -> None:
        if not self._active:
            return super().paintEvent(event)

        p = QPainter(self)
        rect = self.rect()

        # Deep space gradient: dark center radiating slightly lighter edges
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        gradient.setColorAt(0.0, QColor(8, 10, 20))
        gradient.setColorAt(0.3, QColor(11, 13, 23))
        gradient.setColorAt(0.7, QColor(13, 15, 28))
        gradient.setColorAt(1.0, QColor(10, 12, 22))
        p.fillRect(rect, gradient)

        # Subtle radial-ish glow in the upper area (simulated with a vertical gradient)
        glow = QLinearGradient(rect.width() / 2, 0, rect.width() / 2, rect.height() * 0.4)
        glow.setColorAt(0.0, QColor(0, 180, 255, 6))
        glow.setColorAt(1.0, QColor(0, 180, 255, 0))
        p.fillRect(rect, glow)

        p.end()
