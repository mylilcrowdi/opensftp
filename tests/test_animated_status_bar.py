"""
Tests for AnimatedStatusBar and its internal helpers.

Covers: showMessage (text stored, sweep on "…", sweep off otherwise),
        currentMessage, rapid-update collapsing in _FadeLabel,
        _SweepBar visibility / animation state.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from sftp_ui.ui.widgets.animated_status_bar import AnimatedStatusBar, _FadeLabel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture
def bar(qapp):
    b = AnimatedStatusBar()
    yield b
    b._sweep_anim.stop()
    b._label._out.stop()
    b.close()
    QApplication.processEvents()
    b.deleteLater()


@pytest.fixture
def fade_label(qapp):
    lbl = _FadeLabel()
    yield lbl
    lbl._out.stop()
    lbl.close()
    QApplication.processEvents()
    lbl.deleteLater()


# ── AnimatedStatusBar.showMessage ──────────────────────────────────────────────

class TestAnimatedStatusBarShowMessage:
    def test_show_message_stores_text_in_label(self, bar):
        bar.showMessage("Hello world")
        assert bar._label._pending == "Hello world" or bar._label.text() == "Hello world"

    def test_ellipsis_starts_sweep(self, bar):
        bar.showMessage("Scanning…")
        assert bar._sweeping is True

    def test_no_ellipsis_stops_sweep(self, bar):
        bar.showMessage("Scanning…")
        bar.showMessage("Done")
        assert bar._sweeping is False

    def test_sweep_anim_running_on_ellipsis(self, bar):
        bar.showMessage("Loading…")
        assert bar._sweep_anim.state() == QAbstractAnimation.State.Running

    def test_sweep_anim_stopped_without_ellipsis(self, bar):
        bar.showMessage("Loading…")
        bar.showMessage("Ready")
        assert bar._sweep_anim.state() != QAbstractAnimation.State.Running

    def test_sweep_bar_visible_on_ellipsis(self, bar):
        bar.showMessage("Uploading…")
        assert not bar._sweep_bar.isHidden()

    def test_sweep_bar_hidden_without_ellipsis(self, bar):
        bar.showMessage("Uploading…")
        bar.showMessage("Upload complete")
        assert bar._sweep_bar.isHidden()

    def test_repeated_ellipsis_does_not_double_start(self, bar):
        bar.showMessage("Step 1…")
        bar.showMessage("Step 2…")
        assert bar._sweeping is True

    def test_empty_message_stops_sweep(self, bar):
        bar.showMessage("Working…")
        bar.showMessage("")
        assert bar._sweeping is False

    def test_multiple_dots_not_treated_as_ellipsis(self, bar):
        # A regular period is not "…" (Unicode ellipsis U+2026)
        bar.showMessage("Done.")
        assert bar._sweeping is False


# ── currentMessage ─────────────────────────────────────────────────────────────

class TestAnimatedStatusBarCurrentMessage:
    def test_current_message_after_direct_set(self, bar):
        bar._label.setText("direct")
        assert bar.currentMessage() == "direct"

    def test_current_message_empty_initially(self, bar):
        assert bar.currentMessage() == ""


# ── _FadeLabel — rapid update collapsing ──────────────────────────────────────

class TestFadeLabelPending:
    def test_pending_tracks_latest_message(self, fade_label):
        fade_label.set_text("first")
        fade_label.set_text("second")
        fade_label.set_text("third")
        assert fade_label._pending == "third"

    def test_fading_out_true_after_set_text(self, fade_label):
        fade_label.set_text("msg")
        assert fade_label._fading_out is True

    def test_commit_applies_pending_text(self, fade_label):
        fade_label.set_text("committed")
        fade_label._commit()
        assert fade_label.text() == "committed"

    def test_commit_resets_fading_out(self, fade_label):
        fade_label.set_text("x")
        fade_label._commit()
        assert fade_label._fading_out is False

    def test_set_text_while_fading_updates_pending_only(self, fade_label):
        fade_label.set_text("first")
        assert fade_label._fading_out is True
        fade_label.set_text("second")
        assert fade_label._pending == "second"

    def test_out_animation_started_on_set_text(self, fade_label):
        fade_label.set_text("go")
        assert fade_label._out.state() == QAbstractAnimation.State.Running

    def test_initial_opacity_is_one(self, fade_label):
        assert fade_label._opacity == 1.0

    def test_on_tick_updates_opacity(self, fade_label):
        fade_label._on_tick(0.5)
        assert fade_label._opacity == 0.5

    def test_pending_initially_empty(self, fade_label):
        assert fade_label._pending == ""


# ── _start_sweep / _stop_sweep idempotency ────────────────────────────────────

class TestAnimatedStatusBarSweepIdempotency:
    def test_start_sweep_idempotent(self, bar):
        bar._start_sweep()
        bar._start_sweep()
        assert bar._sweeping is True

    def test_stop_sweep_idempotent(self, bar):
        bar._stop_sweep()
        assert bar._sweeping is False

    def test_stop_after_start_resets_flag(self, bar):
        bar._start_sweep()
        bar._stop_sweep()
        assert bar._sweeping is False
