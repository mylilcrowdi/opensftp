"""
Tests for animations/transitions.py — animation preset factory functions.

Covers: fade_in, fade_out, slide_down, appear, pulse_progress,
        ANIMATIONS_ENABLED=False short-circuit, _ensure_opacity_effect reuse,
        duration customisation, start/end values.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, QParallelAnimationGroup, QSequentialAnimationGroup
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel

import sftp_ui.animations.transitions as tr


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def animations_on():
    """Force ANIMATIONS_ENABLED=True for each test; restore afterwards."""
    original = tr.ANIMATIONS_ENABLED
    tr.ANIMATIONS_ENABLED = True
    yield
    tr.ANIMATIONS_ENABLED = original


@pytest.fixture
def widget(qapp):
    import shiboken6
    w = QLabel("test")
    yield w
    w.close()
    if shiboken6.isValid(w):
        shiboken6.delete(w)


# ── _ensure_opacity_effect ────────────────────────────────────────────────────

class TestEnsureOpacityEffect:
    def test_creates_effect_when_absent(self, widget):
        assert widget.graphicsEffect() is None
        effect = tr._ensure_opacity_effect(widget)
        assert isinstance(effect, QGraphicsOpacityEffect)

    def test_sets_effect_on_widget(self, widget):
        tr._ensure_opacity_effect(widget)
        assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    def test_reuses_existing_effect(self, widget):
        e1 = tr._ensure_opacity_effect(widget)
        e2 = tr._ensure_opacity_effect(widget)
        assert e1 is e2

    def test_replaces_non_opacity_effect(self, qapp):
        import shiboken6
        from PySide6.QtWidgets import QGraphicsBlurEffect
        w = QLabel()
        w.setGraphicsEffect(QGraphicsBlurEffect(w))
        effect = tr._ensure_opacity_effect(w)
        assert isinstance(effect, QGraphicsOpacityEffect)
        w.close()
        if shiboken6.isValid(w):
            shiboken6.delete(w)


# ── fade_in ───────────────────────────────────────────────────────────────────

class TestFadeIn:
    def test_returns_animation(self, widget):
        anim = tr.fade_in(widget)
        anim.stop()
        assert anim is not None

    def test_default_duration(self, widget):
        anim = tr.fade_in(widget)
        anim.stop()
        assert anim.duration() == 180

    def test_custom_duration(self, widget):
        anim = tr.fade_in(widget, duration=300)
        anim.stop()
        assert anim.duration() == 300

    def test_start_value_is_zero(self, widget):
        anim = tr.fade_in(widget)
        anim.stop()
        assert anim.startValue() == 0.0

    def test_end_value_is_one(self, widget):
        anim = tr.fade_in(widget)
        anim.stop()
        assert anim.endValue() == 1.0

    def test_installs_opacity_effect(self, widget):
        tr.fade_in(widget).stop()
        assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    def test_disabled_returns_zero_duration(self, widget):
        tr.ANIMATIONS_ENABLED = False
        anim = tr.fade_in(widget)
        assert anim.duration() == 0

    def test_disabled_does_not_install_opacity_effect(self, widget):
        tr.ANIMATIONS_ENABLED = False
        tr.fade_in(widget)
        # No opacity effect should be installed in disabled mode
        assert not isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)


# ── fade_out ──────────────────────────────────────────────────────────────────

class TestFadeOut:
    def test_returns_animation(self, widget):
        anim = tr.fade_out(widget)
        anim.stop()
        assert anim is not None

    def test_default_duration(self, widget):
        anim = tr.fade_out(widget)
        anim.stop()
        assert anim.duration() == 150

    def test_start_value_is_one(self, widget):
        anim = tr.fade_out(widget)
        anim.stop()
        assert anim.startValue() == 1.0

    def test_end_value_is_zero(self, widget):
        anim = tr.fade_out(widget)
        anim.stop()
        assert anim.endValue() == 0.0

    def test_disabled_returns_zero_duration(self, widget):
        tr.ANIMATIONS_ENABLED = False
        anim = tr.fade_out(widget)
        assert anim.duration() == 0


# ── slide_down ────────────────────────────────────────────────────────────────

class TestSlideDown:
    def test_returns_animation(self, widget):
        anim = tr.slide_down(widget)
        anim.stop()
        assert anim is not None

    def test_default_duration(self, widget):
        anim = tr.slide_down(widget)
        anim.stop()
        assert anim.duration() == 220

    def test_end_value_is_current_pos(self, widget):
        pos = widget.pos()
        anim = tr.slide_down(widget)
        anim.stop()
        assert anim.endValue() == pos

    def test_start_value_is_above_end(self, widget):
        pos = widget.pos()
        anim = tr.slide_down(widget)
        anim.stop()
        start = anim.startValue()
        assert start.y() == pos.y() - 12

    def test_disabled_returns_zero_duration(self, widget):
        tr.ANIMATIONS_ENABLED = False
        anim = tr.slide_down(widget)
        assert anim.duration() == 0


# ── appear ────────────────────────────────────────────────────────────────────

class TestAppear:
    def test_returns_parallel_group(self, widget):
        group = tr.appear(widget)
        group.stop()
        assert isinstance(group, QParallelAnimationGroup)

    def test_group_has_two_animations(self, widget):
        group = tr.appear(widget)
        group.stop()
        assert group.animationCount() == 2

    def test_custom_duration_propagated(self, widget):
        group = tr.appear(widget, duration=400)
        group.stop()
        # Both sub-animations should use 400ms
        for i in range(group.animationCount()):
            assert group.animationAt(i).duration() == 400


# ── pulse_progress ────────────────────────────────────────────────────────────

class TestPulseProgress:
    def test_returns_sequential_group(self, widget):
        group = tr.pulse_progress(widget)
        group.stop()
        assert isinstance(group, QSequentialAnimationGroup)

    def test_group_has_two_animations(self, widget):
        group = tr.pulse_progress(widget)
        group.stop()
        assert group.animationCount() == 2

    def test_loop_count_is_infinite(self, widget):
        group = tr.pulse_progress(widget)
        group.stop()
        assert group.loopCount() == -1

    def test_installs_opacity_effect(self, widget):
        group = tr.pulse_progress(widget)
        group.stop()
        assert isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect)

    def test_disabled_returns_empty_group(self, widget):
        tr.ANIMATIONS_ENABLED = False
        group = tr.pulse_progress(widget)
        assert isinstance(group, QSequentialAnimationGroup)
        assert group.animationCount() == 0
