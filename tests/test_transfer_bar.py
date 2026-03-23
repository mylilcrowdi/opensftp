"""
Tests for TransferBar — compact progress + cancel widget.

Covers: initial state, start() (label, visibility), update_progress(),
        set_label(), finish() (bar reset, pulse stopped), cancel_requested
        signal, _on_fade_done() reset.

Animations are disabled globally so tests run synchronously without timers.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

import sftp_ui.animations.transitions as transitions


@pytest.fixture(scope="session", autouse=True)
def disable_animations():
    """Disable all Qt animations for the test session — runs instantly."""
    transitions.ANIMATIONS_ENABLED = False
    yield
    transitions.ANIMATIONS_ENABLED = True


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


# ── Initial state ──────────────────────────────────────────────────────────────

class TestTransferBarInitial:
    def test_hidden_by_default(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        assert not bar.isVisible()

    def test_bar_value_zero_at_init(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        assert bar._bar.value() == 0

    def test_pulse_anim_none_at_init(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        assert bar._pulse_anim is None

    def test_cancel_button_exists(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        assert bar._cancel_btn is not None


# ── start() ────────────────────────────────────────────────────────────────────

class TestTransferBarStart:
    def test_start_makes_visible(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start()
        assert not bar.isHidden()

    def test_start_sets_custom_label(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start("Downloading…")
        assert bar._label.text() == "Downloading…"

    def test_start_default_label(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start()
        assert "Upload" in bar._label.text() or bar._label.text() != ""

    def test_start_resets_bar_to_zero(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar._bar.setValue(75)
        bar.start()
        assert bar._bar.value() == 0

    def test_start_creates_pulse_anim(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start()
        assert bar._pulse_anim is not None

    def test_start_twice_updates_label(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start("First")
        bar.start("Second")
        assert bar._label.text() == "Second"


# ── update_progress() ──────────────────────────────────────────────────────────

class TestTransferBarUpdateProgress:
    def test_update_progress_sets_correct_percentage(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.update_progress(500, 1000)
        assert bar._bar.value() == 50

    def test_update_progress_zero_done(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.update_progress(0, 1000)
        assert bar._bar.value() == 0

    def test_update_progress_full_done(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.update_progress(1000, 1000)
        assert bar._bar.value() == 100

    def test_update_progress_zero_total_does_nothing(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar._bar.setValue(42)
        bar.update_progress(100, 0)
        # total == 0 → guard condition, no change
        assert bar._bar.value() == 42

    def test_update_progress_one_quarter(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.update_progress(256, 1024)
        assert bar._bar.value() == 25

    def test_update_progress_rounding(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.update_progress(1, 3)  # 33.33%
        assert bar._bar.value() == 33


# ── set_label() ────────────────────────────────────────────────────────────────

class TestTransferBarSetLabel:
    def test_set_label_updates_text(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.set_label("Syncing 5 files…")
        assert bar._label.text() == "Syncing 5 files…"

    def test_set_label_empty_string(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.set_label("")
        assert bar._label.text() == ""

    def test_set_label_unicode(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.set_label("Uploading → /remote/path/file.bin")
        assert "→" in bar._label.text()


# ── finish() ───────────────────────────────────────────────────────────────────

class TestTransferBarFinish:
    def test_finish_hides_and_resets_bar(self, qapp):
        # With ANIMATIONS_ENABLED=False the fade-out is 0-duration → _on_fade_done
        # fires synchronously, so bar ends at 0 and widget is hidden.
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start()
        bar.update_progress(300, 1000)
        bar.finish()
        assert bar.isHidden()
        assert bar._bar.value() == 0

    def test_finish_stops_pulse_anim(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start()
        pulse = bar._pulse_anim
        bar.finish()
        assert bar._pulse_anim is None

    def test_finish_without_start_does_not_crash(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.finish()   # _pulse_anim is None — should not raise

    def test_on_fade_done_hides_widget(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar.start()
        bar._on_fade_done()
        assert bar.isHidden()

    def test_on_fade_done_resets_bar(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        bar._bar.setValue(100)
        bar._on_fade_done()
        assert bar._bar.value() == 0


# ── cancel_requested signal ────────────────────────────────────────────────────

class TestTransferBarCancelSignal:
    def test_cancel_button_click_emits_signal(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        received = []
        bar.cancel_requested.connect(lambda: received.append(True))
        bar._cancel_btn.click()
        assert received == [True]

    def test_multiple_cancel_clicks(self, qapp):
        from sftp_ui.ui.widgets.transfer_bar import TransferBar
        bar = TransferBar()
        count = [0]
        bar.cancel_requested.connect(lambda: count.__setitem__(0, count[0] + 1))
        bar._cancel_btn.click()
        bar._cancel_btn.click()
        assert count[0] == 2
