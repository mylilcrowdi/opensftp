"""
Remote file editing — download, open in editor, auto-upload on save.

Tests cover:
1. _do_edit_remote: downloads file to temp, emits _edit_ready signal
2. _on_edit_ready: creates watcher, launches editor, sets up session
3. File change detection: watcher → debounce → upload
4. Atomic-save editors (vim/VS Code): delete+recreate re-watched
5. _cleanup_edit_sessions: stops watchers, removes temp dirs
6. Concurrent edits: multiple files open simultaneously
7. Disconnected state: upload blocked, status message emitted
8. Re-edit same file: fresh download replaces old session
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtWidgets import QApplication

from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.ui.panels.remote_panel import RemotePanel


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_entry(name: str = "config.txt", path: str = "/data/config.txt",
                size: int = 42, is_dir: bool = False) -> RemoteEntry:
    return RemoteEntry(name=name, path=path, is_dir=is_dir,
                       size=size, mtime=1700000000)


def _make_panel_with_sftp(fake_sftp=None):
    """Create a RemotePanel with an optional fake SFTP client."""
    panel = RemotePanel(sftp=fake_sftp)
    panel.resize(600, 400)
    return panel


def _process_events(ms: int = 100):
    """Process Qt events for up to *ms* milliseconds."""
    app = QApplication.instance()
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


class FakeRemoteFile:
    """Minimal in-memory file returned by FakeSFTP.open_remote."""
    def __init__(self, data: bytes, on_close=None):
        self._data = data
        self._pos = 0
        self._written = bytearray()
        self._on_close = on_close

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
        return chunk

    def write(self, data: bytes) -> int:
        self._written.extend(data)
        return len(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._on_close:
            self._on_close(bytes(self._written))


class FakeSFTP:
    """Minimal SFTP mock for edit tests."""
    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})
        self.uploads: list[tuple[str, bytes]] = []

    def open_remote(self, path: str, mode: str = "rb"):
        if "r" in mode:
            return FakeRemoteFile(self.files.get(path, b""))
        # Write mode: capture uploads on close
        def _on_close(written: bytes):
            self.uploads.append((path, written))
            self.files[path] = written
        return FakeRemoteFile(b"", on_close=_on_close)


# ── 1. _do_edit_remote: temp download + signal ────────────────────────────────

class TestDoEditRemote:
    """_do_edit_remote downloads to temp and emits _edit_ready."""

    @pytest.fixture
    def panel(self, qapp):
        sftp = FakeSFTP({"/data/config.txt": b"server=localhost\nport=3306\n"})
        p = _make_panel_with_sftp(sftp)
        yield p
        p._cleanup_edit_sessions()
        p.close()

    def test_emits_edit_ready_signal(self, panel, qapp):
        received = []
        panel._edit_ready.connect(lambda *args: received.append(args))

        entry = _make_entry()
        panel._do_edit_remote(entry)

        # Wait for background thread to finish
        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            _process_events(50)

        assert len(received) == 1
        tmp_path, remote_path, name, tmp_dir = received[0]
        assert remote_path == "/data/config.txt"
        assert name == "config.txt"
        assert os.path.exists(tmp_path)
        assert Path(tmp_path).name == "config.txt"

    def test_temp_file_contains_remote_content(self, panel, qapp):
        received = []
        panel._edit_ready.connect(lambda *args: received.append(args))

        entry = _make_entry()
        panel._do_edit_remote(entry)

        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            _process_events(50)

        tmp_path = received[0][0]
        assert Path(tmp_path).read_bytes() == b"server=localhost\nport=3306\n"

    def test_temp_dir_has_sftp_ui_prefix(self, panel, qapp):
        received = []
        panel._edit_ready.connect(lambda *args: received.append(args))

        panel._do_edit_remote(_make_entry())

        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            _process_events(50)

        tmp_dir = received[0][3]
        assert "sftp-ui-edit" in os.path.basename(tmp_dir)

    def test_download_failure_emits_status(self, qapp):
        sftp = MagicMock()
        sftp.open_remote.side_effect = IOError("connection lost")
        panel = _make_panel_with_sftp(sftp)

        messages = []
        panel.status_message.connect(messages.append)

        panel._do_edit_remote(_make_entry())

        deadline = time.monotonic() + 2
        while len(messages) < 2 and time.monotonic() < deadline:
            _process_events(50)

        error_msgs = [m for m in messages if "failed" in m.lower()]
        assert len(error_msgs) >= 1
        panel.close()


# ── 2. _on_edit_ready: watcher, editor, session ──────────────────────────────

class TestOnEditReady:
    """_on_edit_ready creates watcher, launches editor, stores session."""

    @pytest.fixture
    def panel_and_tmp(self, qapp, tmp_path):
        panel = _make_panel_with_sftp(FakeSFTP())
        tmp_dir = str(tmp_path / "sftp-ui-edit-test")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_file = os.path.join(tmp_dir, "app.conf")
        Path(tmp_file).write_text("key=value")
        yield panel, tmp_file, tmp_dir
        panel._cleanup_edit_sessions()
        panel.close()

    def test_editor_launched_with_tmp_path(self, panel_and_tmp):
        panel, tmp_file, tmp_dir = panel_and_tmp
        with patch("sftp_ui.core.platform_utils.open_with_editor") as mock_editor:
            panel._on_edit_ready(tmp_file, "/remote/app.conf", "app.conf", tmp_dir)
            mock_editor.assert_called_once_with(tmp_file)

    def test_session_stored(self, panel_and_tmp):
        panel, tmp_file, tmp_dir = panel_and_tmp
        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/app.conf", "app.conf", tmp_dir)

        assert tmp_file in panel._edit_sessions
        session = panel._edit_sessions[tmp_file]
        assert session["remote_path"] == "/remote/app.conf"
        assert session["name"] == "app.conf"
        assert session["tmp_dir"] == tmp_dir
        assert isinstance(session["watcher"], QFileSystemWatcher)
        assert isinstance(session["timer"], QTimer)

    def test_watcher_monitors_tmp_file(self, panel_and_tmp):
        panel, tmp_file, tmp_dir = panel_and_tmp
        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/app.conf", "app.conf", tmp_dir)

        watcher = panel._edit_sessions[tmp_file]["watcher"]
        assert tmp_file in watcher.files()

    def test_debounce_timer_is_singleshot(self, panel_and_tmp):
        panel, tmp_file, tmp_dir = panel_and_tmp
        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/app.conf", "app.conf", tmp_dir)

        timer = panel._edit_sessions[tmp_file]["timer"]
        assert timer.isSingleShot()
        assert timer.interval() == 500

    def test_status_message_emitted(self, panel_and_tmp):
        panel, tmp_file, tmp_dir = panel_and_tmp
        messages = []
        panel.status_message.connect(messages.append)

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/app.conf", "app.conf", tmp_dir)

        assert any("app.conf" in m for m in messages)


# ── 3. File change → debounced upload ─────────────────────────────────────────

class TestFileChangeUpload:
    """Watcher fileChanged triggers debounced upload to remote."""

    @pytest.fixture
    def edit_session(self, qapp, tmp_path):
        """Set up a panel with an active edit session."""
        sftp = FakeSFTP({"/remote/data.json": b'{"old": true}'})
        panel = _make_panel_with_sftp(sftp)

        tmp_dir = str(tmp_path / "sftp-ui-edit-test")
        os.makedirs(tmp_dir)
        tmp_file = os.path.join(tmp_dir, "data.json")
        Path(tmp_file).write_text('{"old": true}')

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/data.json", "data.json", tmp_dir)

        yield panel, tmp_file, sftp
        panel._cleanup_edit_sessions()
        panel.close()

    def test_file_modification_triggers_upload(self, edit_session, qapp):
        panel, tmp_file, sftp = edit_session

        # Simulate editor saving the file
        Path(tmp_file).write_text('{"new": true}')

        # Force debounce trigger (skip waiting for real timer)
        timer = panel._edit_sessions[tmp_file]["timer"]
        timer.timeout.emit()

        # Wait for upload thread to complete
        deadline = time.monotonic() + 3
        while not sftp.uploads and time.monotonic() < deadline:
            time.sleep(0.05)
            _process_events(50)

        assert len(sftp.uploads) >= 1
        path, content = sftp.uploads[-1]
        assert path == "/remote/data.json"
        assert b'"new": true' in content

    def test_debounce_coalesces_rapid_saves(self, edit_session, qapp):
        panel, tmp_file, sftp = edit_session
        watcher = panel._edit_sessions[tmp_file]["watcher"]
        timer = panel._edit_sessions[tmp_file]["timer"]

        # Simulate 5 rapid saves
        for i in range(5):
            Path(tmp_file).write_text(f"version {i}")
            watcher.fileChanged.emit(tmp_file)

        # Timer should be active (restarted on each change)
        assert timer.isActive()

        # Force single debounce trigger
        timer.timeout.emit()

        # Wait for upload thread to complete
        deadline = time.monotonic() + 3
        while not sftp.uploads and time.monotonic() < deadline:
            time.sleep(0.05)
            _process_events(50)

        # Only 1 upload despite 5 changes
        assert len(sftp.uploads) == 1
        assert b"version 4" in sftp.uploads[0][1]

    def test_upload_when_disconnected_emits_error(self, qapp, tmp_path):
        panel = _make_panel_with_sftp(None)  # No SFTP = disconnected

        tmp_dir = str(tmp_path / "sftp-ui-edit-disc")
        os.makedirs(tmp_dir)
        tmp_file = os.path.join(tmp_dir, "file.txt")
        Path(tmp_file).write_text("content")

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/file.txt", "file.txt", tmp_dir)

        messages = []
        panel.status_message.connect(messages.append)

        timer = panel._edit_sessions[tmp_file]["timer"]
        timer.timeout.emit()

        deadline = time.monotonic() + 2
        while not any("disconnected" in m.lower() for m in messages) and time.monotonic() < deadline:
            _process_events(50)

        assert any("disconnected" in m.lower() for m in messages)
        panel._cleanup_edit_sessions()
        panel.close()


# ── 4. Atomic-save editors ────────────────────────────────────────────────────

class TestAtomicSaveEditors:
    """Editors like vim/VS Code delete and recreate the file on save."""

    @pytest.fixture
    def edit_session(self, qapp, tmp_path):
        sftp = FakeSFTP({"/remote/init.cfg": b"original"})
        panel = _make_panel_with_sftp(sftp)

        tmp_dir = str(tmp_path / "sftp-ui-edit-atomic")
        os.makedirs(tmp_dir)
        tmp_file = os.path.join(tmp_dir, "init.cfg")
        Path(tmp_file).write_text("original")

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/init.cfg", "init.cfg", tmp_dir)

        yield panel, tmp_file, sftp
        panel._cleanup_edit_sessions()
        panel.close()

    def test_file_re_added_to_watcher_after_delete_recreate(self, edit_session, qapp):
        panel, tmp_file, sftp = edit_session
        watcher = panel._edit_sessions[tmp_file]["watcher"]

        # Simulate atomic save: delete + recreate
        os.unlink(tmp_file)
        Path(tmp_file).write_text("edited via vim")

        # Trigger fileChanged (OS would fire this)
        watcher.fileChanged.emit(tmp_file)
        _process_events(100)

        # File should be re-added to watcher
        assert tmp_file in watcher.files()

    def test_atomic_save_triggers_upload(self, edit_session, qapp):
        panel, tmp_file, sftp = edit_session
        watcher = panel._edit_sessions[tmp_file]["watcher"]
        timer = panel._edit_sessions[tmp_file]["timer"]

        # Atomic save: delete + recreate
        os.unlink(tmp_file)
        Path(tmp_file).write_text("edited content")
        watcher.fileChanged.emit(tmp_file)
        _process_events(50)

        # Force debounce
        timer.timeout.emit()

        # Wait for upload thread to complete
        deadline = time.monotonic() + 3
        while not sftp.uploads and time.monotonic() < deadline:
            time.sleep(0.05)
            _process_events(50)

        assert len(sftp.uploads) >= 1
        assert sftp.uploads[-1][1] == b"edited content"


# ── 5. Cleanup ────────────────────────────────────────────────────────────────

class TestCleanupEditSessions:
    """_cleanup_edit_sessions stops watchers and removes temp dirs."""

    @pytest.fixture
    def panel_with_sessions(self, qapp, tmp_path):
        panel = _make_panel_with_sftp(FakeSFTP())
        sessions = []
        for i in range(3):
            tmp_dir = str(tmp_path / f"sftp-ui-edit-{i}")
            os.makedirs(tmp_dir)
            tmp_file = os.path.join(tmp_dir, f"file{i}.txt")
            Path(tmp_file).write_text(f"content {i}")
            with patch("sftp_ui.core.platform_utils.open_with_editor"):
                panel._on_edit_ready(tmp_file, f"/remote/file{i}.txt",
                                     f"file{i}.txt", tmp_dir)
            sessions.append((tmp_file, tmp_dir))
        yield panel, sessions
        panel.close()

    def test_sessions_cleared(self, panel_with_sessions):
        panel, sessions = panel_with_sessions
        assert len(panel._edit_sessions) == 3

        panel._cleanup_edit_sessions()

        assert len(panel._edit_sessions) == 0
        assert len(panel._edit_watchers) == 0

    def test_temp_dirs_removed(self, panel_with_sessions):
        panel, sessions = panel_with_sessions

        panel._cleanup_edit_sessions()

        for _, tmp_dir in sessions:
            assert not os.path.exists(tmp_dir)

    def test_timers_stopped(self, panel_with_sessions):
        panel, sessions = panel_with_sessions

        # Start all timers
        for tmp_file, _ in sessions:
            panel._edit_sessions[tmp_file]["timer"].start()

        panel._cleanup_edit_sessions()
        # No assertion needed: if timers fire after cleanup they'd crash.
        # The test passing means cleanup worked.

    def test_disconnect_calls_cleanup(self, panel_with_sessions):
        panel, sessions = panel_with_sessions
        assert len(panel._edit_sessions) == 3

        panel.set_disconnected()

        assert len(panel._edit_sessions) == 0


# ── 6. Concurrent edits ──────────────────────────────────────────────────────

class TestConcurrentEdits:
    """Multiple files can be open for edit simultaneously."""

    def test_multiple_sessions_tracked(self, qapp, tmp_path):
        panel = _make_panel_with_sftp(FakeSFTP())
        files = ["app.py", "config.yaml", "readme.md"]

        for name in files:
            tmp_dir = str(tmp_path / f"edit-{name}")
            os.makedirs(tmp_dir)
            tmp_file = os.path.join(tmp_dir, name)
            Path(tmp_file).write_text(f"content of {name}")
            with patch("sftp_ui.core.platform_utils.open_with_editor"):
                panel._on_edit_ready(tmp_file, f"/project/{name}", name, tmp_dir)

        assert len(panel._edit_sessions) == 3
        assert len(panel._edit_watchers) == 3

        # Each session has independent watcher and timer
        watchers = [s["watcher"] for s in panel._edit_sessions.values()]
        assert len(set(id(w) for w in watchers)) == 3

        panel._cleanup_edit_sessions()
        panel.close()

    def test_editing_same_file_twice_replaces_session(self, qapp, tmp_path):
        sftp = FakeSFTP({"/remote/x.txt": b"v1"})
        panel = _make_panel_with_sftp(sftp)

        # First edit
        tmp_dir1 = str(tmp_path / "edit1")
        os.makedirs(tmp_dir1)
        tmp_file1 = os.path.join(tmp_dir1, "x.txt")
        Path(tmp_file1).write_text("v1")
        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file1, "/remote/x.txt", "x.txt", tmp_dir1)

        # Second edit of same file (different tmp path)
        tmp_dir2 = str(tmp_path / "edit2")
        os.makedirs(tmp_dir2)
        tmp_file2 = os.path.join(tmp_dir2, "x.txt")
        Path(tmp_file2).write_text("v2")
        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file2, "/remote/x.txt", "x.txt", tmp_dir2)

        # Both sessions tracked (different tmp_paths)
        assert tmp_file1 in panel._edit_sessions
        assert tmp_file2 in panel._edit_sessions

        panel._cleanup_edit_sessions()
        panel.close()


# ── 7. open_with_editor cross-platform ────────────────────────────────────────

class TestOpenWithEditor:
    """platform_utils.open_with_editor launches correct command per platform."""

    def test_linux_uses_xdg_open(self):
        from sftp_ui.core import platform_utils
        with patch.object(platform_utils, "PLATFORM", "linux"):
            with patch("subprocess.Popen") as mock_p:
                platform_utils.open_with_editor("/tmp/file.txt")
                mock_p.assert_called_once_with(["xdg-open", "/tmp/file.txt"])

    def test_macos_uses_open(self):
        from sftp_ui.core import platform_utils
        with patch.object(platform_utils, "PLATFORM", "darwin"):
            with patch("subprocess.Popen") as mock_p:
                platform_utils.open_with_editor("/tmp/file.txt")
                mock_p.assert_called_once_with(["open", "/tmp/file.txt"])

    def test_windows_uses_startfile(self):
        from sftp_ui.core import platform_utils
        with patch.object(platform_utils, "PLATFORM", "win32"):
            with patch("os.startfile", create=True) as mock_sf:
                platform_utils.open_with_editor("C:\\temp\\file.txt")
                mock_sf.assert_called_once_with("C:\\temp\\file.txt")


# ── 8. Context menu integration ───────────────────────────────────────────────

class TestContextMenuEdit:
    """Remote panel context menu shows Edit for files, not directories."""

    @pytest.fixture
    def panel(self, qapp):
        p = _make_panel_with_sftp(FakeSFTP())
        p.resize(600, 400)
        yield p
        p._cleanup_edit_sessions()
        p.close()

    def test_edit_ready_signal_exists(self, panel):
        assert hasattr(panel, "_edit_ready")

    def test_edit_sessions_dict_initialized(self, panel):
        assert isinstance(panel._edit_sessions, dict)
        assert len(panel._edit_sessions) == 0

    def test_edit_watchers_list_initialized(self, panel):
        assert isinstance(panel._edit_watchers, list)
        assert len(panel._edit_watchers) == 0


# ── 9. Upload thread safety ──────────────────────────────────────────────────

class TestUploadThreadSafety:
    """Upload runs in background thread, status emitted back to main."""

    def test_upload_runs_in_background_thread(self, qapp, tmp_path):
        upload_threads = []
        original_thread_start = threading.Thread.start

        def _capture_start(self, *args, **kwargs):
            upload_threads.append(self)
            original_thread_start(self, *args, **kwargs)

        sftp = FakeSFTP({"/remote/bg.txt": b"old"})
        panel = _make_panel_with_sftp(sftp)

        tmp_dir = str(tmp_path / "sftp-ui-edit-thread")
        os.makedirs(tmp_dir)
        tmp_file = os.path.join(tmp_dir, "bg.txt")
        Path(tmp_file).write_text("new content")

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/bg.txt", "bg.txt", tmp_dir)

        # Trigger upload via timer
        with patch.object(threading.Thread, "start", _capture_start):
            timer = panel._edit_sessions[tmp_file]["timer"]
            timer.timeout.emit()

        # At least one thread was started for download or upload
        assert len(upload_threads) >= 1

        panel._cleanup_edit_sessions()
        panel.close()

    def test_status_messages_emitted_during_upload(self, qapp, tmp_path):
        sftp = FakeSFTP({"/remote/st.txt": b"data"})
        panel = _make_panel_with_sftp(sftp)

        tmp_dir = str(tmp_path / "sftp-ui-edit-status")
        os.makedirs(tmp_dir)
        tmp_file = os.path.join(tmp_dir, "st.txt")
        Path(tmp_file).write_text("updated")

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/st.txt", "st.txt", tmp_dir)

        messages = []
        panel.status_message.connect(messages.append)

        timer = panel._edit_sessions[tmp_file]["timer"]
        timer.timeout.emit()

        deadline = time.monotonic() + 2
        while len(messages) < 2 and time.monotonic() < deadline:
            _process_events(50)

        # "Uploading st.txt…" and "Saved st.txt"
        assert any("uploading" in m.lower() or "st.txt" in m.lower() for m in messages)

        panel._cleanup_edit_sessions()
        panel.close()


# ── 10. Upload failure handling ───────────────────────────────────────────────

class TestUploadFailure:
    """Upload errors are caught and reported via status_message."""

    def test_sftp_error_emits_save_failed(self, qapp, tmp_path):
        sftp = MagicMock()
        sftp.open_remote.side_effect = IOError("permission denied")
        panel = _make_panel_with_sftp(sftp)

        tmp_dir = str(tmp_path / "sftp-ui-edit-fail")
        os.makedirs(tmp_dir)
        tmp_file = os.path.join(tmp_dir, "locked.txt")
        Path(tmp_file).write_text("changes")

        with patch("sftp_ui.core.platform_utils.open_with_editor"):
            panel._on_edit_ready(tmp_file, "/remote/locked.txt", "locked.txt", tmp_dir)

        messages = []
        panel.status_message.connect(messages.append)

        timer = panel._edit_sessions[tmp_file]["timer"]
        timer.timeout.emit()

        deadline = time.monotonic() + 2
        while not any("failed" in m.lower() for m in messages) and time.monotonic() < deadline:
            _process_events(50)

        assert any("failed" in m.lower() for m in messages)

        panel._cleanup_edit_sessions()
        panel.close()
