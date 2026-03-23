"""
StatusDot — animated connection-state indicator for the toolbar.

States and their animations:
  idle        grey  · static
  connecting  blue  · opacity pulse (InOutSine, 700 ms loop)
  connected   green · scale pop 1.7 → 1.0 (OutBack, 450 ms)
  failed      red   · horizontal shake (7 steps × 40 ms)
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QPointF, QPropertyAnimation,
    QSequentialAnimationGroup, Qt,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

_COLORS = {
    "idle":       "#585b70",
    "connecting": "#89b4fa",
    "connected":  "#a6e3a1",
    "failed":     "#f38ba8",
}


class StatusDot(QWidget):
    """12 × 12 px animated dot that reflects the current connection state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip("Not connected")

        self._color   = QColor(_COLORS["idle"])
        self._opacity = 1.0
        self._scale   = 1.0
        self._shake   = 0.0

        # ── Pulse (CONNECTING) ────────────────────────────────────────────────
        self._pulse = QSequentialAnimationGroup(self)
        for start, end in ((1.0, 0.3), (0.3, 1.0)):
            a = QPropertyAnimation(self, b"dot_opacity", self)
            a.setDuration(700)
            a.setStartValue(start)
            a.setEndValue(end)
            a.setEasingCurve(QEasingCurve.Type.InOutSine)
            self._pulse.addAnimation(a)
        self._pulse.setLoopCount(-1)

        # ── Pop (CONNECTED) ───────────────────────────────────────────────────
        self._pop = QPropertyAnimation(self, b"dot_scale", self)
        self._pop.setDuration(450)
        self._pop.setStartValue(1.7)
        self._pop.setEndValue(1.0)
        self._pop.setEasingCurve(QEasingCurve.Type.OutBack)

        # ── Shake (FAILED) ────────────────────────────────────────────────────
        self._shake_anim = QSequentialAnimationGroup(self)
        steps = [0.0, 5.0, -5.0, 4.0, -4.0, 2.0, -2.0, 0.0]
        for i in range(len(steps) - 1):
            a = QPropertyAnimation(self, b"dot_shake", self)
            a.setDuration(40)
            a.setStartValue(steps[i])
            a.setEndValue(steps[i + 1])
            self._shake_anim.addAnimation(a)

    # ── Animatable properties ─────────────────────────────────────────────────

    @Property(float)
    def dot_opacity(self) -> float:
        return self._opacity

    @dot_opacity.setter
    def dot_opacity(self, v: float) -> None:
        self._opacity = v
        self.update()

    @Property(float)
    def dot_scale(self) -> float:
        return self._scale

    @dot_scale.setter
    def dot_scale(self, v: float) -> None:
        self._scale = v
        self.update()

    @Property(float)
    def dot_shake(self) -> float:
        return self._shake

    @dot_shake.setter
    def dot_shake(self, v: float) -> None:
        self._shake = v
        self.update()

    # ── State transitions ─────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._pulse.stop()
        self._pop.stop()
        self._shake_anim.stop()
        self._opacity = 1.0
        self._scale   = 1.0
        self._shake   = 0.0

    def set_idle(self) -> None:
        self._reset()
        self._color = QColor(_COLORS["idle"])
        self.setToolTip("Not connected")
        self.update()

    def set_connecting(self) -> None:
        self._reset()
        self._color = QColor(_COLORS["connecting"])
        self.setToolTip("Connecting…")
        self.update()
        self._pulse.start()

    def set_connected(self) -> None:
        self._reset()
        self._color = QColor(_COLORS["connected"])
        self._scale = 1.7          # pop animation starts from here
        self.setToolTip("Connected")
        self.update()
        self._pop.start()

    def set_failed(self) -> None:
        self._reset()
        self._color = QColor(_COLORS["failed"])
        self.setToolTip("Connection failed")
        self.update()
        self._shake_anim.start()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width()  / 2.0 + self._shake
        cy = self.height() / 2.0
        r  = (min(self.width(), self.height()) / 2.0 - 1.0) * self._scale
        p.setOpacity(self._opacity)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)
