"""
Tests for MainWindow — close-event guard and toolbar combo constraints.

Covers:
  - closeEvent blocks the close when transfers are in progress and the user
    declines the confirmation (event.ignore() is called).
  - closeEvent allows the close when no transfers are in progress.
  - closeEvent allows the close when the user confirms cancellation.
  - Connection combo has a maximum width so long names don't crush the toolbar.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from sftp_ui.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _make_window(qapp):
    """Return a MainWindow with no connections and no SFTP client."""
    with patch("sftp_ui.core.connection.ConnectionStore._load"):
        win = MainWindow()
    return win


# ── closeEvent guard ─────────────────────────────────────────────────────────

class TestCloseEventGuard:
    def test_close_allowed_when_no_queue(self, qapp):
        """Window closes immediately when no queue is active."""
        win = _make_window(qapp)
        event = QCloseEvent()
        win.closeEvent(event)
        assert event.isAccepted()
        win.destroy()

    def test_close_allowed_when_queue_is_none(self, qapp):
        """closeEvent with _queue=None must accept the event."""
        win = _make_window(qapp)
        win._queue = None
        event = QCloseEvent()
        win.closeEvent(event)
        assert event.isAccepted()
        win.destroy()

    def test_close_blocked_when_user_declines(self, qapp):
        """When transfers are running and user clicks No, event must be ignored."""
        win = _make_window(qapp)

        # Create a mock queue that reports 2 pending transfers
        mock_queue = MagicMock()
        mock_queue.pending_count.return_value = 2
        win._queue = mock_queue

        # Patch QMessageBox.question to simulate the user clicking No
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ):
            event = QCloseEvent()
            win.closeEvent(event)

        # The close must have been rejected because _queue is still set
        assert not event.isAccepted()
        win._queue = None
        win.destroy()

    def test_close_allowed_when_user_confirms(self, qapp):
        """When user clicks Yes to cancel transfers, event must be accepted."""
        win = _make_window(qapp)

        mock_queue = MagicMock()
        mock_queue.pending_count.return_value = 1
        win._queue = mock_queue

        # Patch QMessageBox.question to simulate the user clicking Yes
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            event = QCloseEvent()
            win.closeEvent(event)

        assert event.isAccepted()
        win.destroy()

    def test_close_allowed_when_queue_has_no_pending(self, qapp):
        """A queue with 0 pending transfers does not block the close."""
        win = _make_window(qapp)

        mock_queue = MagicMock()
        mock_queue.pending_count.return_value = 0
        mock_queue.stop = MagicMock()
        win._queue = mock_queue

        event = QCloseEvent()
        win.closeEvent(event)
        assert event.isAccepted()
        win.destroy()


# ── Connection combo max-width ────────────────────────────────────────────────

class TestConnComboMaxWidth:
    def test_combo_has_maximum_width_set(self, qapp):
        """The combo must have a maximum width to prevent toolbar overflow."""
        win = _make_window(qapp)
        assert win._conn_combo.maximumWidth() < 10_000  # not the Qt default (16M)
        win.destroy()

    def test_combo_max_width_does_not_exceed_400(self, qapp):
        """Max width should be <= 400 px — enough for most names, not toolbar-busting."""
        win = _make_window(qapp)
        assert win._conn_combo.maximumWidth() <= 400
        win.destroy()

    def test_combo_min_width_still_reasonable(self, qapp):
        """Minimum width must stay at least 150 px so short names are legible."""
        win = _make_window(qapp)
        assert win._conn_combo.minimumWidth() >= 150
        win.destroy()
