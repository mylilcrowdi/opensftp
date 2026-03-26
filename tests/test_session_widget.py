"""
Tests for SessionWidget — the per-tab SFTP session container.

Covers:
  - Initial state (not connected, no queue)
  - connect_to: success and failure flows via mocked SFTPClient
  - disconnect: clears state, returns True/False
  - on_connect_success: sets auto_reconnect, starts health timer
  - pause/resume toggle via _on_pause_resume
  - cancel delegates to queue.cancel_current
  - health check guard conditions
  - health check triggers reconnect thread when connection is dead
  - reconnect signals are forwarded to status_message
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer

from sftp_ui.core.connection import Connection
from sftp_ui.ui.session_widget import SessionWidget


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_widget(qapp):
    return SessionWidget()


def _fake_conn() -> Connection:
    return Connection(name="test", host="127.0.0.1", user="u", port=22)


# ── initial state ─────────────────────────────────────────────────────────────

class TestInitialState:
    def test_not_connected(self, qapp):
        w = _make_widget(qapp)
        assert not w.is_connected

    def test_active_conn_is_none(self, qapp):
        w = _make_widget(qapp)
        assert w.active_conn is None

    def test_queue_is_none(self, qapp):
        w = _make_widget(qapp)
        assert w._queue is None

    def test_auto_reconnect_disabled(self, qapp):
        w = _make_widget(qapp)
        assert not w._auto_reconnect

    def test_has_transfer_panel(self, qapp):
        w = _make_widget(qapp)
        assert w.transfer_panel is not None

    def test_has_local_panel(self, qapp):
        w = _make_widget(qapp)
        assert w.local_panel is not None

    def test_has_remote_panel(self, qapp):
        w = _make_widget(qapp)
        assert w.remote_panel is not None


# ── is_connected property ─────────────────────────────────────────────────────

class TestIsConnected:
    def test_false_when_sftp_none(self, qapp):
        w = _make_widget(qapp)
        w._sftp = None
        assert not w.is_connected

    def test_true_when_sftp_connected(self, qapp):
        w = _make_widget(qapp)
        mock_sftp = MagicMock()
        mock_sftp.is_connected.return_value = True
        w._sftp = mock_sftp
        assert w.is_connected

    def test_false_when_sftp_not_connected(self, qapp):
        w = _make_widget(qapp)
        mock_sftp = MagicMock()
        mock_sftp.is_connected.return_value = False
        w._sftp = mock_sftp
        assert not w.is_connected


# ── connect_to ────────────────────────────────────────────────────────────────

def _sync_thread(target, daemon=True, name=None, **kwargs):
    """Replace threading.Thread with synchronous execution for safe Qt testing."""
    class _FakeThread:
        def __init__(self, fn):
            self._fn = fn
        def start(self):
            self._fn()
    return _FakeThread(target)


class TestConnectTo:
    """
    connect_to() spawns a background thread.  We patch threading.Thread so the
    target runs synchronously in the test thread — avoids cross-thread Qt signal
    issues that cause segfaults when the widget is GC'd mid-thread.
    """

    def test_sets_active_conn_before_thread(self, qapp):
        """active_conn is set synchronously, before the connect thread runs."""
        w = _make_widget(qapp)
        conn = _fake_conn()

        with patch("sftp_ui.ui.session_widget.threading.Thread", side_effect=_sync_thread):
            with patch("sftp_ui.ui.session_widget.SFTPClient") as MockSFTP:
                MockSFTP.return_value.connect.side_effect = ConnectionError("refused")
                w.connect_to(conn)

        assert w.active_conn is conn

    def test_success_emits_connect_success(self, qapp):
        w = _make_widget(qapp)
        emitted: list[bool] = []

        w._signals.connect_success.connect(lambda: emitted.append(True))

        with patch("sftp_ui.ui.session_widget.threading.Thread", side_effect=_sync_thread):
            with patch("sftp_ui.ui.session_widget.SFTPClient") as MockSFTP:
                MockSFTP.return_value.connect.return_value = None
                with patch.object(w, "_setup_queue"):
                    w.connect_to(_fake_conn())

        assert emitted

    def test_failure_emits_connect_failed(self, qapp):
        w = _make_widget(qapp)
        failed_msgs: list[str] = []

        w._signals.connect_failed.connect(failed_msgs.append)

        with patch("sftp_ui.ui.session_widget.threading.Thread", side_effect=_sync_thread):
            with patch("sftp_ui.ui.session_widget.SFTPClient") as MockSFTP:
                MockSFTP.return_value.connect.side_effect = OSError("connection refused")
                w.connect_to(_fake_conn())

        assert failed_msgs
        assert "connection refused" in failed_msgs[0]

    def test_failure_does_not_set_sftp(self, qapp):
        w = _make_widget(qapp)

        with patch("sftp_ui.ui.session_widget.threading.Thread", side_effect=_sync_thread):
            with patch("sftp_ui.ui.session_widget.SFTPClient") as MockSFTP:
                MockSFTP.return_value.connect.side_effect = OSError("nope")
                w.connect_to(_fake_conn())

        assert w._sftp is None


# ── disconnect ────────────────────────────────────────────────────────────────

class TestDisconnect:
    def test_returns_true_when_no_queue(self, qapp):
        w = _make_widget(qapp)
        assert w.disconnect() is True

    def test_clears_sftp(self, qapp):
        w = _make_widget(qapp)
        mock_sftp = MagicMock()
        mock_sftp.is_connected.return_value = True
        w._sftp = mock_sftp
        w._active_conn = _fake_conn()
        w.disconnect()
        assert w._sftp is None

    def test_clears_active_conn(self, qapp):
        w = _make_widget(qapp)
        w._sftp = MagicMock()
        w._sftp.is_connected.return_value = True
        w._active_conn = _fake_conn()
        w.disconnect()
        assert w._active_conn is None

    def test_disables_auto_reconnect(self, qapp):
        w = _make_widget(qapp)
        w._auto_reconnect = True
        w.disconnect()
        assert not w._auto_reconnect

    def test_stops_health_timer(self, qapp):
        w = _make_widget(qapp)
        w._health_timer.start()
        assert w._health_timer.isActive()
        w.disconnect()
        assert not w._health_timer.isActive()

    def test_stops_and_clears_queue(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        mock_queue.pending_count.return_value = 0
        w._queue = mock_queue
        w.disconnect()
        mock_queue.stop.assert_called_once()
        assert w._queue is None

    def test_emits_connection_changed_none(self, qapp):
        w = _make_widget(qapp)
        received: list = []
        w.connection_changed.connect(received.append)
        w.disconnect()
        assert received == [None]


# ── on_connect_success ────────────────────────────────────────────────────────

class TestOnConnectSuccess:
    def test_enables_auto_reconnect(self, qapp):
        w = _make_widget(qapp)
        w._active_conn = _fake_conn()
        w.on_connect_success()
        assert w._auto_reconnect is True

    def test_clears_reconnecting_flag(self, qapp):
        w = _make_widget(qapp)
        w._reconnecting = True
        w._active_conn = _fake_conn()
        w.on_connect_success()
        assert not w._reconnecting

    def test_starts_health_timer(self, qapp):
        w = _make_widget(qapp)
        w._active_conn = _fake_conn()
        w.on_connect_success()
        assert w._health_timer.isActive()
        w._health_timer.stop()

    def test_emits_connection_changed(self, qapp):
        w = _make_widget(qapp)
        conn = _fake_conn()
        w._active_conn = conn
        received: list = []
        w.connection_changed.connect(received.append)
        w.on_connect_success()
        assert received == [conn]
        w._health_timer.stop()


# ── pause / resume ────────────────────────────────────────────────────────────

class TestPauseResume:
    def test_no_queue_does_not_raise(self, qapp):
        w = _make_widget(qapp)
        w._on_pause_resume()   # should not raise

    def test_pauses_when_running(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        mock_queue.is_paused.return_value = False
        w._queue = mock_queue
        w._on_pause_resume()
        mock_queue.pause.assert_called_once()

    def test_unpauses_when_paused(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        mock_queue.is_paused.return_value = True
        w._queue = mock_queue
        w._on_pause_resume()
        mock_queue.unpause.assert_called_once()

    def test_does_not_call_both_pause_and_unpause(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        mock_queue.is_paused.return_value = False
        w._queue = mock_queue
        w._on_pause_resume()
        mock_queue.unpause.assert_not_called()

    def test_pause_updates_transfer_panel(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        mock_queue.is_paused.return_value = False
        w._queue = mock_queue
        with patch.object(w.transfer_panel, "set_paused") as mock_set:
            w._on_pause_resume()
        mock_set.assert_called_once_with(True)

    def test_unpause_updates_transfer_panel(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        mock_queue.is_paused.return_value = True
        w._queue = mock_queue
        with patch.object(w.transfer_panel, "set_paused") as mock_set:
            w._on_pause_resume()
        mock_set.assert_called_once_with(False)


# ── cancel ────────────────────────────────────────────────────────────────────

class TestCancel:
    def test_no_queue_does_not_raise(self, qapp):
        w = _make_widget(qapp)
        w._on_cancel()

    def test_delegates_to_queue_cancel_current(self, qapp):
        w = _make_widget(qapp)
        mock_queue = MagicMock()
        w._queue = mock_queue
        w._on_cancel()
        mock_queue.cancel_current.assert_called_once()


# ── connection health check ───────────────────────────────────────────────────

class TestConnectionHealthCheck:
    def test_no_op_when_sftp_none(self, qapp):
        w = _make_widget(qapp)
        w._sftp = None
        w._auto_reconnect = True
        w._check_connection_health()   # must not raise

    def test_no_op_when_auto_reconnect_disabled(self, qapp):
        w = _make_widget(qapp)
        w._auto_reconnect = False
        mock_sftp = MagicMock()
        w._sftp = mock_sftp
        w._check_connection_health()
        mock_sftp.is_alive.assert_not_called()

    def test_no_op_when_already_reconnecting(self, qapp):
        w = _make_widget(qapp)
        w._auto_reconnect = True
        w._reconnecting = True
        mock_sftp = MagicMock()
        w._sftp = mock_sftp
        w._check_connection_health()
        mock_sftp.is_alive.assert_not_called()

    def test_no_op_when_connection_alive(self, qapp):
        w = _make_widget(qapp)
        w._auto_reconnect = True
        w._reconnecting = False
        mock_sftp = MagicMock()
        mock_sftp.is_alive.return_value = True
        w._sftp = mock_sftp
        w._check_connection_health()
        assert not w._reconnecting

    def test_sets_reconnecting_when_dead(self, qapp):
        w = _make_widget(qapp)
        w._auto_reconnect = True
        w._reconnecting = False
        mock_sftp = MagicMock()
        mock_sftp.is_alive.return_value = False
        w._sftp = mock_sftp
        w._active_conn = _fake_conn()

        reconnect_called = threading.Event()
        w._do_reconnect = reconnect_called.set  # neutralise — avoid real reconnect

        # Patch Thread so target runs synchronously (no dangling threads / Qt GC issues)
        with patch("sftp_ui.ui.session_widget.threading.Thread", side_effect=_sync_thread):
            w._check_connection_health()

        assert w._reconnecting is True
        assert reconnect_called.is_set(), "reconnect thread was not started"


# ── reconnect signals ─────────────────────────────────────────────────────────

class TestReconnectSignals:
    def test_reconnecting_signal_emits_status(self, qapp):
        w = _make_widget(qapp)
        msgs: list[str] = []
        w.status_message.connect(msgs.append)
        w._on_reconnecting()
        assert any("reconnect" in m.lower() for m in msgs)

    def test_reconnected_signal_emits_status(self, qapp):
        w = _make_widget(qapp)
        msgs: list[str] = []
        w.status_message.connect(msgs.append)
        with patch.object(w.remote_panel, "refresh"):
            w._on_reconnected()
        assert any("reconnect" in m.lower() for m in msgs)

    def test_reconnect_failed_signal_emits_error(self, qapp):
        w = _make_widget(qapp)
        msgs: list[str] = []
        w.status_message.connect(msgs.append)
        w._on_reconnect_failed("timed out")
        assert any("timed out" in m for m in msgs)
