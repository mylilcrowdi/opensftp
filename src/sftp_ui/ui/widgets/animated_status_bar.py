"""
AnimatedStatusBar — status bar with a sweeping activity indicator and
crossfading text transitions.

Activity indicator
------------------
A 2 px bar at the bottom edge sweeps left → right using InOutSine easing
(accelerates in the middle, eases at both ends — the "quadratic slope" feel).
The sweep loops indefinitely while any operation is in progress, then stops.

Auto-detection heuristic
  A message that contains "…" is treated as "in progress" → sweep starts.
  Any other message → sweep stops.
This works automatically with all existing showMessage() calls — no
call-site changes required anywhere else in the codebase.

Text transitions
----------------
Every message change crossfades:
  fade-out 90 ms  InQuad  (old text exits quickly)
  → swap text
  → fade-in 160 ms OutQuad (new text arrives gently)

Rapid updates (e.g. per-file delete progress) are collapsed: only the latest
pending text is shown after the current fade completes, so the animation never
queues up or falls behind.

QPainter note
-------------
QGraphicsOpacityEffect is NOT used here.  Applying a graphics effect to a
child widget inside a parent that overrides paintEvent causes nested-painter
crashes ("A paint device can only be painted by one painter at a time").
Instead _FadeLabel paints itself with p.setOpacity(), and the sweep bar is a
separate transparent child widget so each widget has exactly one painter.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRect, QTimer, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QStatusBar, QWidget


# ── Crossfading label ──────────────────────────────────────────────────────────

class _FadeLabel(QLabel):
    """QLabel that crossfades between old and new text on every change.

    Uses p.setOpacity() in a custom paintEvent instead of
    QGraphicsOpacityEffect to avoid nested-painter conflicts.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._opacity: float = 1.0
        self._pending: str = ""
        self._fading_out: bool = False

        # Fade-out: InQuad — starts slow, ends fast (text disappears crisply)
        self._out = QVariantAnimation(self)
        self._out.setDuration(90)
        self._out.setStartValue(1.0)
        self._out.setEndValue(0.0)
        self._out.setEasingCurve(QEasingCurve.Type.InQuad)
        self._out.valueChanged.connect(self._on_tick)
        self._out.finished.connect(self._commit)

        # Fade-in: OutQuad — starts fast, ends slow (text arrives gently)
        self._in = QVariantAnimation(self)
        self._in.setDuration(160)
        self._in.setStartValue(0.0)
        self._in.setEndValue(1.0)
        self._in.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._in.valueChanged.connect(self._on_tick)

    def _on_tick(self, v: float) -> None:
        self._opacity = float(v)
        self.update()

    def set_text(self, text: str) -> None:
        self._pending = text
        if self._fading_out:
            return
        self._in.stop()
        self._fading_out = True
        self._out.start()

    def _commit(self) -> None:
        self._fading_out = False
        self.setText(self._pending)
        self._in.start()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.setPen(self.palette().windowText().color())
        p.setFont(self.font())
        p.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.text(),
        )


# ── Sweep bar overlay ──────────────────────────────────────────────────────────

class _SweepBar(QWidget):
    """Transparent 2 px child widget that draws the sweeping highlight beam.

    Lives at the bottom edge of the status bar.  Being a separate widget
    means its paintEvent has its own painter — no conflict with the parent.
    """

    _SWEEP_WIDTH  = 0.28   # beam width as fraction of bar width
    _SWEEP_RANGE  = 1.60   # total travel distance (> 1 so beam enters/exits fully)
    _SWEEP_OFFSET = 0.30   # left overshoot

    def __init__(self, parent: QStatusBar) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pos: float = 0.0
        self.hide()

    def set_pos(self, v: float) -> None:
        self._pos = float(v)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        w  = self.width()
        cx = (self._pos * self._SWEEP_RANGE - self._SWEEP_OFFSET) * w
        hw = w * self._SWEEP_WIDTH

        grad = QLinearGradient(cx - hw, 0.0, cx + hw, 0.0)
        grad.setColorAt(0.00, QColor(0, 0, 0, 0))
        grad.setColorAt(0.35, QColor(137, 180, 250, 80))
        grad.setColorAt(0.50, QColor(137, 180, 250, 210))
        grad.setColorAt(0.65, QColor(137, 180, 250, 80))
        grad.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.fillRect(QRect(0, 0, w, self.height()), grad)


# ── Animated status bar ────────────────────────────────────────────────────────

class AnimatedStatusBar(QStatusBar):
    """
    Drop-in replacement for QStatusBar.

    showMessage(text) is the only public API change needed — everything else
    is backwards-compatible.  The sweep animation and text transitions are
    purely visual; no behaviour is changed.
    """

    _BAR_HEIGHT = 2  # px

    # Informational messages (no "…", not errors) are cleared after this delay.
    _INFO_TIMEOUT_MS = 5_000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizeGripEnabled(False)

        self._label = _FadeLabel(self)
        self.addWidget(self._label, stretch=1)

        self._sweep_bar = _SweepBar(self)
        self._sweeping: bool = False

        self._sweep_anim = QVariantAnimation(self)
        self._sweep_anim.setStartValue(0.0)
        self._sweep_anim.setEndValue(1.0)
        self._sweep_anim.setDuration(1100)
        self._sweep_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._sweep_anim.setLoopCount(-1)
        self._sweep_anim.valueChanged.connect(self._sweep_bar.set_pos)

        # Auto-clear timer: fires once to blank informational messages.
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(lambda: self._label.set_text(""))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sweep_bar.setGeometry(0, self.height() - self._BAR_HEIGHT,
                                    self.width(), self._BAR_HEIGHT)

    # ── Public API ─────────────────────────────────────────────────────────────

    def showMessage(self, text: str, timeout: int = 0) -> None:  # type: ignore[override]
        self._label.set_text(text)
        self._clear_timer.stop()
        if "…" in text:
            # In-progress messages (contain "…") drive the sweep and never
            # auto-clear — they are replaced when the operation finishes.
            self._start_sweep()
        else:
            self._stop_sweep()
            # Error-like messages (contain "failed", "error", "denied") stay
            # until replaced; purely informational messages auto-clear.
            lower = text.lower()
            is_error = any(w in lower for w in ("fail", "error", "denied", "cancel", "disconnect"))
            if text and not is_error:
                self._clear_timer.start(self._INFO_TIMEOUT_MS)

    def currentMessage(self) -> str:
        return self._label.text()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _start_sweep(self) -> None:
        if not self._sweeping:
            self._sweeping = True
            self._sweep_bar.show()
            self._sweep_bar.raise_()
            self._sweep_anim.start()

    def _stop_sweep(self) -> None:
        if self._sweeping:
            self._sweeping = False
            self._sweep_anim.stop()
            self._sweep_bar.hide()
