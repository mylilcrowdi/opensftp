"""
Tests for SmoothProgressBar — animated QProgressBar.

Covers: initial value, forward setValue triggers animation,
        backward setValue is instant (no animation regression),
        value-at-or-below-current is instant, invisible widget is instant.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from sftp_ui.ui.widgets.smooth_progress_bar import SmoothProgressBar


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


class TestSmoothProgressBarBasic:
    def test_initial_value_is_minus_one_before_range(self, qapp):
        # Qt QProgressBar default before any range/value: -1
        bar = SmoothProgressBar()
        assert bar.value() == -1

    def test_set_value_after_range_is_instant_when_hidden(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(50)
        assert bar.value() == 50

    def test_backward_jump_is_instant(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(80)   # hidden → instant
        bar.setValue(20)   # backward → instant
        assert bar.value() == 20

    def test_backward_stops_animation(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(50)   # hidden → instant, value is 50
        bar.show()
        bar.setValue(70)   # forward → starts animation
        bar.setValue(30)   # backward → must be instant, stop animation
        assert bar._anim.state() != QAbstractAnimation.State.Running
        bar.hide()

    def test_reset_to_zero_is_instant(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(75)
        bar.setValue(0)
        assert bar.value() == 0

    def test_same_value_is_instant(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(40)
        bar.setValue(40)
        assert bar.value() == 40

    def test_range_respected(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(100)
        assert bar.value() == 100

    def test_value_never_exceeds_maximum(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(200)
        assert bar.value() <= 100


class TestSmoothProgressBarAnimation:
    def test_forward_on_visible_widget_starts_animation(self, qapp):
        bar = SmoothProgressBar()
        bar.show()
        bar.setValue(0)
        bar.setValue(80)
        assert bar._anim.state() == QAbstractAnimation.State.Running
        bar._anim.stop()
        bar.hide()

    def test_forward_on_hidden_widget_is_instant(self, qapp):
        bar = SmoothProgressBar()
        # Widget is hidden (default)
        bar.setValue(60)
        assert bar._anim.state() != QAbstractAnimation.State.Running
        assert bar.value() == 60

    def test_animation_end_value_is_target(self, qapp):
        bar = SmoothProgressBar()
        bar.show()
        bar.setValue(0)
        bar.setValue(70)
        assert bar._anim.endValue() == 70
        bar._anim.stop()
        bar.hide()

    def test_animation_start_value_is_current(self, qapp):
        bar = SmoothProgressBar()
        bar.setRange(0, 100)
        bar.setValue(30)   # hidden → instant, value is now 30
        bar.show()
        bar.setValue(80)   # visible, 80 > 30 → animation starts from 30
        assert bar._anim.startValue() == 30
        bar._anim.stop()
        bar.hide()
