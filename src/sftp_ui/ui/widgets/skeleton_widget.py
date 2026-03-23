"""
SkeletonWidget — shimmer-animated loading placeholder for directory listings.

Shown as an overlay on the remote file table while listdir is in flight.
A QVariantAnimation drives a shimmer gradient that sweeps left → right
across placeholder rows, giving the impression of content arriving.

All drawing is QPainter — no QGraphicsEffect, no child widgets, minimal
overhead.  The shimmer animation starts/stops automatically with show/hide.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRect, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

# Row geometry
_ROW_H      = 14    # height of each placeholder bar
_ROW_GAP    = 12    # gap between bars
_ROW_LEFT   = 12    # left margin
_ROW_TOP    = 18    # top offset (below the header row)
_ROW_RADIUS = 5

# Width fractions (relative to widget width) — varied for organic feel
_WIDTHS = [0.72, 0.45, 0.88, 0.58, 0.77, 0.63, 0.82, 0.51, 0.69, 0.40,
           0.75, 0.55, 0.84, 0.48, 0.70]

_COLOR_BASE     = QColor("#313244")
_COLOR_SHIMMER  = QColor("#4a4d65")   # slightly lighter — the highlight peak
_COLOR_BG       = QColor("#1e1e2e")   # matches table background


class SkeletonWidget(QWidget):
    """Overlay widget that covers the file table while a listing loads."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Shimmer position: 0.0 = left edge, 1.0 = right edge
        self._shimmer = 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1400)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v: float) -> None:
        self._shimmer = float(v)
        self.update()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._shimmer = 0.0
        self._anim.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._anim.stop()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Solid background — hides table content beneath
        p.fillRect(self.rect(), _COLOR_BG)

        w = self.width()

        # Shimmer gradient: travels from -30% to 130% of width
        gx     = (self._shimmer * 1.6 - 0.3) * w
        gwidth = w * 0.30
        grad = QLinearGradient(gx - gwidth, 0.0, gx + gwidth, 0.0)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(0.5, _COLOR_SHIMMER)
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))

        y = _ROW_TOP
        for w_frac in _WIDTHS:
            if y + _ROW_H > self.height():
                break
            row_w = max(40, int(w * w_frac) - _ROW_LEFT)
            rect  = QRect(_ROW_LEFT, y, row_w, _ROW_H)

            # Base bar
            p.setBrush(_COLOR_BASE)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, _ROW_RADIUS, _ROW_RADIUS)

            # Shimmer highlight clipped to this bar's bounds
            p.setBrush(grad)
            p.drawRoundedRect(rect, _ROW_RADIUS, _ROW_RADIUS)

            y += _ROW_H + _ROW_GAP
