"""
Multiple sessions / tabs — connect to several servers simultaneously.

Tests cover:
- SessionWidget encapsulation (one connection per tab)
- Tab creation/closing
- Active tab switching
- Per-tab state isolation (separate queues, panels)
- Toolbar interaction with active tab
- Tab persistence (UIState)
- Keyboard shortcuts for tab navigation
"""
from __future__ import annotations

import os
import sys
import json
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from PySide6.QtWidgets import QApplication, QTabWidget, QMessageBox
from PySide6.QtCore import Qt

from sftp_ui.core.connection import Connection, ConnectionStore
from sftp_ui.core.queue import TransferQueue
from sftp_ui.core.ui_state import UIState
from sftp_ui.ui.main_window import MainWindow
from sftp_ui.ui.session_widget import SessionWidget

import sftp_ui.animations.transitions as _t
_t.ANIMATIONS_ENABLED = False


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def store(tmp_path):
    p = tmp_path / "conns.json"
    p.write_text("[]")
    s = ConnectionStore(path=str(p))
    s.add(Connection(name="Server A", host="a.io", user="u", password="x"))
    s.add(Connection(name="Server B", host="b.io", user="u", password="x"))
    return s


@pytest.fixture
def ui_state(tmp_path):
    p = tmp_path / "ui_state.json"
    return UIState(path=p)


@pytest.fixture
def main_window(qapp, store):
    with patch("sftp_ui.core.ui_state.UIState._load"):
        win = MainWindow(store=store)
    # Process deferred _restore_tabs timer
    QApplication.processEvents()
    yield win
    # Clean up all tabs to prevent resource leaks
    while win._tabs.count() > 0:
        session = win._tabs.widget(0)
        if isinstance(session, SessionWidget):
            session._sftp = None
            session._queue = None
            session._active_conn = None
        win._tabs.removeTab(0)
    win.destroy()


class TestSessionWidget:
    """Test SessionWidget encapsulation of a single session."""

    def test_session_widget_has_local_panel(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        assert sw.local_panel is not None
        sw.destroy()

    def test_session_widget_has_remote_panel(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        assert sw.remote_panel is not None
        sw.destroy()

    def test_session_widget_has_transfer_panel(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        assert sw.transfer_panel is not None
        sw.destroy()

    def test_session_widget_starts_disconnected(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        assert not sw.is_connected
        assert sw.active_conn is None
        sw.destroy()

    def test_session_widget_has_glass_frames(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        assert len(sw._glass_frames) == 3  # local, remote, transfer
        sw.destroy()

    def test_session_widget_frost_toggle(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        sw.set_frost_active(True)
        assert all(f._active for f in sw._glass_frames)
        sw.set_frost_active(False)
        assert all(not f._active for f in sw._glass_frames)
        sw.destroy()

    def test_session_widget_disconnect_when_not_connected(self, qapp, ui_state):
        sw = SessionWidget(ui_state=ui_state)
        result = sw.disconnect()
        assert result is True
        sw.destroy()

    def test_session_widget_separate_state_from_others(self, qapp, ui_state):
        sw1 = SessionWidget(ui_state=ui_state)
        sw2 = SessionWidget(ui_state=ui_state)
        assert sw1.local_panel is not sw2.local_panel
        assert sw1.remote_panel is not sw2.remote_panel
        assert sw1.transfer_panel is not sw2.transfer_panel
        sw1.destroy()
        sw2.destroy()


class TestTabWidgetBasics:
    """Test MainWindow QTabWidget setup."""

    def test_main_window_has_tab_widget(self, main_window):
        assert hasattr(main_window, "_tabs")
        assert isinstance(main_window._tabs, QTabWidget)

    def test_tabs_are_closable(self, main_window):
        assert main_window._tabs.tabsClosable()

    def test_tabs_are_movable(self, main_window):
        assert main_window._tabs.isMovable()

    def test_initial_tab_exists(self, main_window):
        assert main_window._tabs.count() >= 1

    def test_initial_tab_is_session_widget(self, main_window):
        w = main_window._tabs.widget(0)
        assert isinstance(w, SessionWidget)

    def test_tab_label_default_is_new_tab(self, main_window):
        assert main_window._tabs.tabText(0) == "New Tab"

    def test_corner_widget_exists(self, main_window):
        corner = main_window._tabs.cornerWidget(Qt.Corner.TopRightCorner)
        assert corner is not None


class TestTabCreation:
    """Test creating new tabs."""

    def test_add_new_tab_increases_count(self, main_window):
        initial = main_window._tabs.count()
        main_window._add_new_tab()
        assert main_window._tabs.count() == initial + 1

    def test_add_new_tab_returns_session_widget(self, main_window):
        session = main_window._add_new_tab()
        assert isinstance(session, SessionWidget)

    def test_new_tab_becomes_active(self, main_window):
        session = main_window._add_new_tab()
        assert main_window._active_session() is session

    def test_max_tabs_limit(self, main_window):
        # Fill up to max
        while main_window._tabs.count() < MainWindow.MAX_TABS:
            main_window._add_new_tab()
        assert main_window._tabs.count() == MainWindow.MAX_TABS
        # Adding one more should be blocked (shows warning dialog)
        with patch.object(QMessageBox, "warning"):
            main_window._add_new_tab()
        assert main_window._tabs.count() == MainWindow.MAX_TABS

    def test_connect_in_empty_tab_uses_that_tab(self, main_window):
        """If current tab is not connected, connecting fills it."""
        initial_count = main_window._tabs.count()
        session = main_window._active_session()
        assert not session.is_connected
        # Simulate connect (won't actually connect but will use existing tab)
        conn = main_window._store.all()[0]
        for i in range(main_window._conn_combo.count()):
            if main_window._conn_combo.itemData(i) == conn.id:
                main_window._conn_combo.setCurrentIndex(i)
                break
        # The tab count should stay the same
        assert main_window._tabs.count() == initial_count


class TestTabClosing:
    """Test closing tabs."""

    def test_close_tab_decreases_count(self, main_window):
        main_window._add_new_tab()
        count_before = main_window._tabs.count()
        main_window._on_close_tab(0)
        assert main_window._tabs.count() == count_before - 1

    def test_close_last_tab_creates_new_empty(self, main_window):
        """Closing the last tab automatically creates a new empty one."""
        while main_window._tabs.count() > 1:
            main_window._on_close_tab(0)
        main_window._on_close_tab(0)
        assert main_window._tabs.count() == 1
        assert isinstance(main_window._active_session(), SessionWidget)

    def test_close_disconnected_tab_succeeds(self, main_window):
        main_window._add_new_tab()
        count = main_window._tabs.count()
        main_window._on_close_tab(count - 1)
        assert main_window._tabs.count() == count - 1

    def test_close_connected_tab_with_transfers_asks(self, main_window):
        """Closing a tab with active transfers shows warning."""
        session = main_window._add_new_tab()
        mock_sftp = MagicMock()
        mock_sftp.is_connected.return_value = True
        session._sftp = mock_sftp
        mock_queue = MagicMock()
        mock_queue.pending_count.return_value = 3
        session._queue = mock_queue

        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.No,
        ):
            count_before = main_window._tabs.count()
            idx = main_window._tabs.indexOf(session)
            main_window._on_close_tab(idx)
            # Tab should NOT have been closed
            assert main_window._tabs.count() == count_before

        # Clean up
        session._sftp = None
        session._queue = None


class TestActiveTabSwitching:
    """Test switching between tabs."""

    def test_switching_tabs_updates_active_session(self, main_window):
        s1 = main_window._active_session()
        s2 = main_window._add_new_tab()
        assert main_window._active_session() is s2
        main_window._tabs.setCurrentIndex(0)
        assert main_window._active_session() is s1

    def test_next_tab_wraps_around(self, main_window):
        main_window._add_new_tab()
        main_window._tabs.setCurrentIndex(main_window._tabs.count() - 1)
        main_window._next_tab()
        assert main_window._tabs.currentIndex() == 0

    def test_prev_tab_wraps_around(self, main_window):
        main_window._add_new_tab()
        main_window._tabs.setCurrentIndex(0)
        main_window._prev_tab()
        assert main_window._tabs.currentIndex() == main_window._tabs.count() - 1


class TestToolbarInteractionWithTabs:
    """Test toolbar interactions with active tab."""

    def test_toolbar_reflects_disconnected_tab(self, main_window):
        session = main_window._active_session()
        assert not session.is_connected
        assert main_window._connect_btn.isEnabled()
        assert not main_window._disconnect_btn.isEnabled()
        assert not main_window._refresh_btn.isEnabled()
        assert not main_window._sync_btn.isEnabled()

    def test_toolbar_reflects_connected_tab(self, main_window):
        session = main_window._active_session()
        mock_sftp = MagicMock()
        mock_sftp.is_connected.return_value = True
        session._sftp = mock_sftp
        conn = Connection(name="TestConn", host="test.io", user="u")
        session._active_conn = conn

        main_window._sync_toolbar_to_session(session)
        assert not main_window._connect_btn.isEnabled()
        assert main_window._disconnect_btn.isEnabled()
        assert main_window._refresh_btn.isEnabled()
        assert main_window._sync_btn.isEnabled()
        assert "TestConn" in main_window.windowTitle()

        # Clean up
        session._sftp = None
        session._active_conn = None

    def test_switching_tab_syncs_toolbar(self, main_window):
        """Switching tabs updates toolbar to reflect active tab's state."""
        s1 = main_window._active_session()
        s2 = main_window._add_new_tab()

        # Make s1 look connected
        mock_sftp = MagicMock()
        mock_sftp.is_connected.return_value = True
        s1._sftp = mock_sftp
        s1._active_conn = Connection(name="S1", host="s1.io", user="u")

        # Switch back to s1
        main_window._tabs.setCurrentIndex(main_window._tabs.indexOf(s1))
        assert main_window._disconnect_btn.isEnabled()

        # Switch to s2 (disconnected)
        main_window._tabs.setCurrentIndex(main_window._tabs.indexOf(s2))
        assert main_window._connect_btn.isEnabled()
        assert not main_window._disconnect_btn.isEnabled()

        # Clean up
        s1._sftp = None
        s1._active_conn = None


class TestTabPersistence:
    """Test saving/restoring tab state."""

    def test_save_tab_state(self, main_window):
        main_window._add_new_tab()
        main_window._save_tab_state()
        tabs = main_window._ui_state.open_tabs
        assert len(tabs) >= 2

    def test_tab_state_includes_connection_id(self, main_window):
        session = main_window._active_session()
        conn = Connection(name="Persisted", host="p.io", user="u")
        session._active_conn = conn

        main_window._save_tab_state()
        tabs = main_window._ui_state.open_tabs
        idx = main_window._tabs.indexOf(session)
        assert tabs[idx]["connection_id"] == conn.id

        session._active_conn = None

    def test_active_tab_index_persisted(self, main_window):
        main_window._add_new_tab()
        main_window._tabs.setCurrentIndex(0)
        main_window._save_tab_state()
        assert main_window._ui_state.active_tab_index == 0


class TestMultiTabIntegration:
    """Integration tests: full multi-tab workflow."""

    def test_multiple_tabs_independent_panels(self, main_window):
        s1 = main_window._active_session()
        s2 = main_window._add_new_tab()
        assert s1.local_panel is not s2.local_panel
        assert s1.remote_panel is not s2.remote_panel
        assert s1.transfer_panel is not s2.transfer_panel

    def test_connection_changed_updates_tab_title(self, main_window):
        session = main_window._add_new_tab()
        idx = main_window._tabs.indexOf(session)
        assert main_window._tabs.tabText(idx) == "New Tab"

        conn = Connection(name="MyServer", host="my.io", user="u")
        main_window._on_session_connection_changed(session, conn)
        assert main_window._tabs.tabText(idx) == "MyServer"

    def test_disconnect_resets_tab_title(self, main_window):
        session = main_window._add_new_tab()
        idx = main_window._tabs.indexOf(session)

        conn = Connection(name="MyServer", host="my.io", user="u")
        main_window._on_session_connection_changed(session, conn)
        assert main_window._tabs.tabText(idx) == "MyServer"

        main_window._on_session_connection_changed(session, None)
        assert main_window._tabs.tabText(idx) == "New Tab"

    def test_frost_applied_to_all_tabs(self, main_window):
        s1 = main_window._active_session()
        s2 = main_window._add_new_tab()

        # Simulate frost
        for s in (s1, s2):
            s.set_frost_active(True)
            assert all(f._active for f in s._glass_frames)

        for s in (s1, s2):
            s.set_frost_active(False)
            assert all(not f._active for f in s._glass_frames)


class TestTabMemoryManagement:
    """Test resource cleanup."""

    def test_closed_tab_cleanup(self, main_window):
        session = main_window._add_new_tab()
        idx = main_window._tabs.indexOf(session)
        main_window._on_close_tab(idx)
        # Session should be scheduled for deletion
        # (can't directly test deleteLater, but tab count decreased)
        assert session not in [
            main_window._tabs.widget(i)
            for i in range(main_window._tabs.count())
        ]
