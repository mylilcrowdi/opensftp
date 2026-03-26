"""
Tests for RemotePanel operations and state transitions.

Covers: _show_info() dialog content, set_disconnected(), _on_listdir_done()
        (dotdot injection, path_changed signal, status_message, sort preserved),
        _on_listdir_error() breadcrumb update, navigate_or_root() fallback.
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox as _RealQMB

from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.ui.panels.remote_panel import RemotePanel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def panel(qapp):
    """Function-scoped panel with explicit cleanup to avoid Qt segfaults.

    shiboken6.delete() synchronously destroys the C++ object, avoiding the
    race between gc.collect() / processEvents() and the Python wrapper that
    causes segfaults on Python 3.12 + PySide6 6.x.
    """
    import shiboken6
    p = RemotePanel()
    yield p
    p._skeleton._anim.stop()
    p.close()
    if shiboken6.isValid(p):
        shiboken6.delete(p)


def _entry(name: str, *, is_dir=False, size=0, mtime=1_700_000_000, path=None) -> RemoteEntry:
    p = path or f"/remote/{name}"
    return RemoteEntry(name=name, path=p, is_dir=is_dir, size=size, mtime=mtime)


def _run_info(panel: RemotePanel, entry: RemoteEntry) -> str:
    captured = []

    class FakeBox:
        Icon = _RealQMB.Icon

        def __init__(self, parent):
            pass
        def setWindowTitle(self, t): pass
        def setText(self, t): captured.append(t)
        def setIcon(self, i): pass
        def exec(self): pass

    with patch("sftp_ui.ui.panels.remote_panel.QMessageBox", FakeBox):
        panel._show_info(entry)

    return captured[0] if captured else ""


# ── _show_info() ──────────────────────────────────────────────────────────────

class TestRemoteShowInfo:
    def test_info_contains_name(self, panel):
        e = _entry("report.csv", size=1024)
        assert "report.csv" in _run_info(panel, e)

    def test_info_contains_path(self, panel):
        e = _entry("data.bin", path="/srv/uploads/data.bin", size=42)
        assert "/srv/uploads/data.bin" in _run_info(panel, e)

    def test_info_file_shows_file_type(self, panel):
        e = _entry("f.txt", is_dir=False, size=10)
        assert "File" in _run_info(panel, e)

    def test_info_directory_shows_directory_type(self, panel):
        e = _entry("photos", is_dir=True)
        assert "Directory" in _run_info(panel, e)

    def test_info_file_shows_size(self, panel):
        e = _entry("big.bin", size=4096)
        assert "4" in _run_info(panel, e)  # "4 KB" or similar

    def test_info_directory_shows_dash_for_size(self, panel):
        e = _entry("uploads", is_dir=True)
        assert "—" in _run_info(panel, e)

    def test_info_shows_modified_date(self, panel):
        e = _entry("timed.log", mtime=1_700_000_000)
        text = _run_info(panel, e)
        assert "Modified" in text or "2023" in text


# ── set_disconnected() ────────────────────────────────────────────────────────

class TestRemotePanelDisconnect:
    def test_set_disconnected_clears_model(self, panel):
        panel._on_listdir_done("/remote", [_entry("file.txt")], 0)
        assert panel._model.rowCount() > 0
        panel.set_disconnected()
        assert panel._model.rowCount() == 0

    def test_set_disconnected_resets_cwd(self, panel):
        panel._on_listdir_done("/remote/subdir", [], 0)
        panel.set_disconnected()
        assert panel.current_path() == "/"

    def test_set_disconnected_clears_sftp(self, panel):
        panel._sftp = MagicMock()
        panel.set_disconnected()
        assert panel._sftp is None

    def test_set_disconnected_shows_empty_state(self, panel):
        panel._empty_state.hide()
        panel.set_disconnected()
        assert not panel._empty_state.isHidden()


# ── _on_listdir_done() ────────────────────────────────────────────────────────

class TestListdirDone:
    def test_loads_entries_into_model(self, panel):
        entries = [_entry("a.txt"), _entry("b.txt")]
        panel._on_listdir_done("/remote", entries, 0)
        # 2 entries + 1 dotdot (path != "/")
        assert panel._model.rowCount() == 3

    def test_dotdot_not_added_at_root(self, panel):
        panel._on_listdir_done("/", [_entry("home", is_dir=True, path="/home")], 0)
        names = [panel._model.entry(i).name for i in range(panel._model.rowCount())]
        assert ".." not in names

    def test_dotdot_added_for_subpath(self, panel):
        panel._on_listdir_done("/remote/subdir", [], 0)
        names = [panel._model.entry(i).name for i in range(panel._model.rowCount())]
        assert ".." in names

    def test_dotdot_points_to_parent(self, panel):
        panel._on_listdir_done("/remote/sub", [], 0)
        dotdot = panel._model.entry(0)
        assert dotdot.path == "/remote"

    def test_updates_cwd(self, panel):
        panel._on_listdir_done("/remote/target", [], 0)
        assert panel.current_path() == "/remote/target"

    def test_emits_path_changed(self, panel):
        received = []
        panel.path_changed.connect(received.append)
        panel._on_listdir_done("/remote/x", [], 0)
        assert received == ["/remote/x"]

    def test_emits_status_message(self, panel):
        messages = []
        panel.status_message.connect(messages.append)
        panel._on_listdir_done("/remote", [_entry("sub", is_dir=True), _entry("f.txt")], 0)
        assert len(messages) >= 1
        msg = messages[-1]
        assert "folder" in msg or "file" in msg

    def test_status_message_counts_correctly(self, panel):
        messages = []
        panel.status_message.connect(messages.append)
        entries = [
            _entry("d1", is_dir=True),
            _entry("d2", is_dir=True),
            _entry("f1.txt"),
            _entry("f2.txt"),
            _entry("f3.txt"),
        ]
        panel._on_listdir_done("/remote", entries, 0)
        msg = messages[-1]
        assert "2" in msg  # 2 folders
        assert "3" in msg  # 3 files

    def test_dotfiles_filtered_by_default(self, panel):
        panel._on_listdir_done("/remote", [_entry(".hidden"), _entry("visible.txt")], 0)
        names = [panel._model.entry(i).name for i in range(panel._model.rowCount())]
        assert ".hidden" not in names
        assert "visible.txt" in names

    def test_sort_persists_across_listdir(self, panel):
        """Sort state is preserved when navigating between directories.

        Column sort persistence (roadmap #7): the user's chosen sort column
        must survive navigation so the panel looks consistent across dirs.
        """
        from PySide6.QtCore import Qt
        # Simulate a pre-existing sort chosen by the user
        panel._sort_col = 0
        panel._sort_order = Qt.SortOrder.AscendingOrder
        panel._on_listdir_done("/remote", [_entry("z.txt"), _entry("a.txt"), _entry("m.txt")], 0)
        # Sort state is preserved — col=0, ascending
        assert panel._sort_col == 0
        assert panel._sort_order == Qt.SortOrder.AscendingOrder
        # Entries must be sorted by name (ascending): a, m, z
        names = [panel._model.entry(i).name for i in range(panel._model.rowCount())
                 if panel._model.entry(i).name != ".."]
        assert names == ["a.txt", "m.txt", "z.txt"]


# ── _on_listdir_error() ───────────────────────────────────────────────────────

class TestListdirError:
    def test_hides_skeleton_on_error(self, panel):
        panel._skeleton.show()
        panel._on_listdir_error("/remote/bad", "Connection reset", 0)
        assert panel._skeleton.isHidden()

    def test_breadcrumb_restored_to_current_path_on_error(self, panel):
        # Navigation failure must restore the breadcrumb to the last valid path
        # (_cwd), not corrupt it with the error message string.
        panel._cwd = "/home/user"
        panel._breadcrumb.set_path("/home/user/attempting")  # optimistic set during navigate()
        panel._on_listdir_error("/home/user/attempting", "Connection reset", 0)
        assert panel._breadcrumb._path == "/home/user"

    def test_error_emitted_as_status_message(self, panel):
        messages = []
        panel.status_message.connect(messages.append)
        panel._on_listdir_error("/remote/bad", "Connection reset", 0)
        assert any("Connection reset" in m for m in messages)

    def test_model_not_cleared_on_error(self, panel):
        """Error during navigation must not wipe out the current listing."""
        panel._on_listdir_done("/remote", [_entry("keep.txt")], 0)
        count_before = panel._model.rowCount()
        panel._on_listdir_error("/remote/bad", "oops", 0)
        assert panel._model.rowCount() == count_before


# ── current_path() ────────────────────────────────────────────────────────────

class TestRemotePanelCurrentPath:
    def test_initial_path_is_root(self, panel):
        assert panel.current_path() == "/"

    def test_current_path_updates_after_listdir_done(self, panel):
        panel._on_listdir_done("/srv/data", [], 0)
        assert panel.current_path() == "/srv/data"
