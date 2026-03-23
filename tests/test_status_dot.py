"""
Tests for StatusDot — animated connection-state indicator.

Covers: initial state, all four state transitions (idle / connecting /
connected / failed), color mapping, tooltip text, animation activation.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtGui import QColor

_COLORS = {
    "idle":       "#585b70",
    "connecting": "#89b4fa",
    "connected":  "#a6e3a1",
    "failed":     "#f38ba8",
}


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    return app


# ── Initial state ──────────────────────────────────────────────────────────────

class TestStatusDotInitial:
    def test_initial_color_is_idle(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        assert dot._color == QColor(_COLORS["idle"])

    def test_initial_tooltip_not_connected(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        assert "Not connected" in dot.toolTip()

    def test_initial_opacity_is_one(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        assert dot._opacity == 1.0

    def test_initial_scale_is_one(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        assert dot._scale == 1.0

    def test_initial_shake_is_zero(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        assert dot._shake == 0.0

    def test_fixed_size_12x12(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        assert dot.width() == 12
        assert dot.height() == 12


# ── set_idle ───────────────────────────────────────────────────────────────────

class TestStatusDotIdle:
    def test_idle_color(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()   # change state first
        dot.set_idle()
        assert dot._color == QColor(_COLORS["idle"])

    def test_idle_tooltip(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        dot.set_idle()
        assert "Not connected" in dot.toolTip()

    def test_idle_resets_opacity(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot._opacity = 0.3
        dot.set_idle()
        assert dot._opacity == 1.0

    def test_idle_resets_scale(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot._scale = 1.7
        dot.set_idle()
        assert dot._scale == 1.0

    def test_idle_stops_pulse(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()   # starts pulse
        dot.set_idle()
        assert dot._pulse.state() != QAbstractAnimation.State.Running


# ── set_connecting ─────────────────────────────────────────────────────────────

class TestStatusDotConnecting:
    def test_connecting_color(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()
        assert dot._color == QColor(_COLORS["connecting"])

    def test_connecting_tooltip(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()
        assert "Connecting" in dot.toolTip()

    def test_connecting_starts_pulse(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()
        assert dot._pulse.state() == QAbstractAnimation.State.Running

    def test_connecting_stops_previous_animations(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()   # starts pop
        dot.set_connecting()
        assert dot._pop.state() != QAbstractAnimation.State.Running


# ── set_connected ──────────────────────────────────────────────────────────────

class TestStatusDotConnected:
    def test_connected_color(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        assert dot._color == QColor(_COLORS["connected"])

    def test_connected_tooltip(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        assert "Connected" in dot.toolTip()

    def test_connected_starts_pop_animation(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        assert dot._pop.state() == QAbstractAnimation.State.Running

    def test_connected_stops_pulse(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()
        dot.set_connected()
        assert dot._pulse.state() != QAbstractAnimation.State.Running

    def test_connected_initial_scale_above_one(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        # pop animation starts from scale > 1.0
        assert dot._scale > 1.0


# ── set_failed ─────────────────────────────────────────────────────────────────

class TestStatusDotFailed:
    def test_failed_color(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_failed()
        assert dot._color == QColor(_COLORS["failed"])

    def test_failed_tooltip(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_failed()
        assert "failed" in dot.toolTip().lower()

    def test_failed_starts_shake_animation(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_failed()
        assert dot._shake_anim.state() == QAbstractAnimation.State.Running

    def test_failed_stops_pulse(self, qapp):
        from PySide6.QtCore import QAbstractAnimation
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connecting()
        dot.set_failed()
        assert dot._pulse.state() != QAbstractAnimation.State.Running


# ── State transitions ──────────────────────────────────────────────────────────

class TestStatusDotTransitions:
    def test_connected_then_idle(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        dot.set_idle()
        assert dot._color == QColor(_COLORS["idle"])
        assert "Not connected" in dot.toolTip()

    def test_failed_then_connecting(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_failed()
        dot.set_connecting()
        assert dot._color == QColor(_COLORS["connecting"])

    def test_full_lifecycle(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_idle()
        dot.set_connecting()
        dot.set_connected()
        dot.set_idle()
        assert dot._color == QColor(_COLORS["idle"])

    def test_multiple_connects_in_a_row(self, qapp):
        from sftp_ui.ui.widgets.status_dot import StatusDot
        dot = StatusDot()
        dot.set_connected()
        dot.set_connected()   # should not crash
        assert dot._color == QColor(_COLORS["connected"])
