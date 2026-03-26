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
        layout.setContentsMargins(1, 1, 1, 1)
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

        # Glass fill: noticeably lighter than the void background
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        # Gradient fill: clearly lifted from the ~#04050c void
        glass_fill = QLinearGradient(0, rect.y(), 0, rect.bottom())
        glass_fill.setColorAt(0.0, QColor(30, 34, 65, 230))   # lighter top
        glass_fill.setColorAt(0.3, QColor(22, 26, 52, 220))   # mid
        glass_fill.setColorAt(1.0, QColor(16, 19, 40, 225))   # darker bottom
        p.fillPath(path, glass_fill)

        # Top edge highlight: visible shimmer (the "light reflection on glass")
        p.setPen(Qt.PenStyle.NoPen)
        highlight_rect = QRectF(rect.x() + r, rect.y() + 0.5,
                                rect.width() - 2 * r, 1.0)
        highlight_grad = QLinearGradient(highlight_rect.left(), 0,
                                         highlight_rect.right(), 0)
        highlight_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        highlight_grad.setColorAt(0.3, QColor(160, 210, 255, 50))
        highlight_grad.setColorAt(0.5, QColor(180, 230, 255, 70))
        highlight_grad.setColorAt(0.7, QColor(160, 210, 255, 50))
        highlight_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(highlight_rect, highlight_grad)

        # Border: visible glass edge, brighter with subtle cyan
        border_color = QColor(120, 160, 220, 50)
        p.setPen(QPen(border_color, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, r, r)

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

        # Deep void: very dark base that contrasts with glass panels
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        gradient.setColorAt(0.0, QColor(4, 5, 12))
        gradient.setColorAt(0.5, QColor(6, 7, 16))
        gradient.setColorAt(1.0, QColor(5, 6, 14))
        p.fillRect(rect, gradient)

        # Subtle cyan ambient glow from top-center
        glow = QLinearGradient(rect.width() / 2, 0, rect.width() / 2, rect.height() * 0.5)
        glow.setColorAt(0.0, QColor(0, 160, 255, 8))
        glow.setColorAt(1.0, QColor(0, 160, 255, 0))
        p.fillRect(rect, glow)

        # Faint diagonal light streak
        streak = QLinearGradient(0, 0, rect.width(), rect.height() * 0.3)
        streak.setColorAt(0.0, QColor(100, 120, 200, 3))
        streak.setColorAt(0.5, QColor(100, 120, 200, 0))
        p.fillRect(rect, streak)

        p.end()
