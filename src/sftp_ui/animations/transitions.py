"""
Transitions — named animation presets for UI components.

Design principles:
- Widgets never construct QPropertyAnimation themselves
- They call Transitions.fade_in(widget) etc. and get back a ready-to-start anim
- All durations are centralised here → one place to speed up / slow down / disable
- ANIMATIONS_ENABLED=False short-circuits everything (useful for accessibility or tests)
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation,
    QParallelAnimationGroup, QSequentialAnimationGroup,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# ── Global switch ─────────────────────────────────────────────────────────────
ANIMATIONS_ENABLED: bool = True


def _noop() -> None:
    """Return a zero-duration animation that does nothing (animations disabled)."""
    anim = QPropertyAnimation()
    anim.setDuration(0)
    return anim


# ── Opacity helpers ───────────────────────────────────────────────────────────

def _ensure_opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    return effect


# ── Public presets ────────────────────────────────────────────────────────────

def fade_in(widget: QWidget, duration: int = 180) -> QPropertyAnimation:
    """Fade a widget from invisible to fully visible."""
    if not ANIMATIONS_ENABLED:
        effect = widget.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)
        return _noop()
    effect = _ensure_opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    return anim


def fade_out(widget: QWidget, duration: int = 150) -> QPropertyAnimation:
    """Fade a widget out. Connect finished to widget.hide() if needed."""
    if not ANIMATIONS_ENABLED:
        return _noop()
    effect = _ensure_opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)
    return anim


def slide_down(widget: QWidget, duration: int = 220) -> QPropertyAnimation:
    """Slide a widget in from slightly above its final position."""
    if not ANIMATIONS_ENABLED:
        return _noop()
    start = widget.pos() - QPoint(0, 12)
    anim = QPropertyAnimation(widget, b"pos", widget)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(widget.pos())
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    return anim


def appear(widget: QWidget, duration: int = 220) -> QParallelAnimationGroup:
    """Fade in + slide down combined — use for panels appearing on screen."""
    group = QParallelAnimationGroup(widget)
    group.addAnimation(fade_in(widget, duration))
    group.addAnimation(slide_down(widget, duration))
    return group


def pulse_progress(widget: QWidget) -> QSequentialAnimationGroup:
    """Subtle opacity pulse for an active progress bar."""
    if not ANIMATIONS_ENABLED:
        return QSequentialAnimationGroup()
    effect = _ensure_opacity_effect(widget)
    effect.setOpacity(1.0)

    down = QPropertyAnimation(effect, b"opacity", widget)
    down.setDuration(600)
    down.setStartValue(1.0)
    down.setEndValue(0.55)
    down.setEasingCurve(QEasingCurve.Type.InOutSine)

    up = QPropertyAnimation(effect, b"opacity", widget)
    up.setDuration(600)
    up.setStartValue(0.55)
    up.setEndValue(1.0)
    up.setEasingCurve(QEasingCurve.Type.InOutSine)

    group = QSequentialAnimationGroup(widget)
    group.addAnimation(down)
    group.addAnimation(up)
    group.setLoopCount(-1)  # infinite
    return group
