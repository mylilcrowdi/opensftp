"""
SmoothProgressBar — QProgressBar that animates value changes.

setValue() glides to the new value with OutCubic easing instead of jumping.
Backward jumps (reset / new batch) are applied instantly so the bar never
appears to go backwards.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QVariantAnimation
from PySide6.QtWidgets import QProgressBar

_DURATION_MS = 120


class SmoothProgressBar(QProgressBar):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Drive the C++ setValue directly — bypasses our Python override
        # so there is no recursive call.
        self._anim.valueChanged.connect(
            lambda v: QProgressBar.setValue(self, int(v))
        )

    def setValue(self, value: int) -> None:
        current = self.value()
        if value <= current or not self.isVisible():
            # Instant: backward jump, reset, or widget not yet shown
            self._anim.stop()
            QProgressBar.setValue(self, value)
            return
        self._anim.stop()
        self._anim.setStartValue(current)
        self._anim.setEndValue(value)
        self._anim.start()
