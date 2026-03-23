"""
Tests for SkeletonWidget — shimmer-animated loading placeholder.

Covers: initial state, animation starts on show / stops on hide,
        shimmer value tracked via _tick(), resize behaviour,
        paint does not crash for various sizes.
"""
from __future__ import annotations

import sys
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from sftp_ui.ui.widgets.skeleton_widget import SkeletonWidget


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


# ── initial state ──────────────────────────────────────────────────────────────

class TestSkeletonWidgetInit:
    def test_shimmer_starts_at_zero(self, qapp):
        w = SkeletonWidget()
        assert w._shimmer == 0.0

    def test_animation_not_running_initially(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        w = SkeletonWidget()
        assert w._anim.state() != QAbstractAnimation.State.Running

    def test_widget_is_hidden_by_default(self, qapp):
        w = SkeletonWidget()
        assert w.isHidden()


# ── animation lifecycle ────────────────────────────────────────────────────────

class TestSkeletonWidgetAnimation:
    def test_animation_starts_on_show(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        w = SkeletonWidget()
        w.resize(400, 300)
        w.show()
        assert w._anim.state() == QAbstractAnimation.State.Running
        w.hide()

    def test_animation_stops_on_hide(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        w = SkeletonWidget()
        w.resize(400, 300)
        w.show()
        w.hide()
        assert w._anim.state() != QAbstractAnimation.State.Running

    def test_shimmer_reset_to_zero_on_show(self, qapp):
        w = SkeletonWidget()
        w._shimmer = 0.75  # manually set
        w.resize(400, 300)
        w.show()
        assert w._shimmer == 0.0
        w.hide()

    def test_animation_loops_indefinitely(self, qapp):
        w = SkeletonWidget()
        assert w._anim.loopCount() == -1

    def test_animation_duration_positive(self, qapp):
        w = SkeletonWidget()
        assert w._anim.duration() > 0


# ── _tick() ────────────────────────────────────────────────────────────────────

class TestSkeletonWidgetTick:
    def test_tick_updates_shimmer_value(self, qapp):
        w = SkeletonWidget()
        w._tick(0.42)
        assert w._shimmer == pytest.approx(0.42)

    def test_tick_accepts_zero(self, qapp):
        w = SkeletonWidget()
        w._tick(0.0)
        assert w._shimmer == 0.0

    def test_tick_accepts_one(self, qapp):
        w = SkeletonWidget()
        w._tick(1.0)
        assert w._shimmer == 1.0


# ── paint robustness ──────────────────────────────────────────────────────────

class TestSkeletonWidgetPaint:
    def test_paint_does_not_crash_normal_size(self, qapp):
        w = SkeletonWidget()
        w.resize(600, 400)
        w.show()
        w.repaint()
        w.hide()

    def test_paint_does_not_crash_small_size(self, qapp):
        """Widget smaller than one row — no rows drawn, no crash."""
        w = SkeletonWidget()
        w.resize(10, 5)
        w.show()
        w.repaint()
        w.hide()

    def test_paint_does_not_crash_at_shimmer_extremes(self, qapp):
        w = SkeletonWidget()
        w.resize(400, 300)
        w.show()
        for val in (0.0, 0.5, 1.0):
            w._shimmer = val
            w.repaint()
        w.hide()
