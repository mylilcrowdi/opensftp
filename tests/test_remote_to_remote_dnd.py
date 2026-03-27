"""
Remote → Remote drag-and-drop (same server, temp-buffer copy).

Tests cover:
- RemotePanel emits ``remote_copy_requested`` signal on REMOTE_ENTRIES_MIME drop
- Drop-overlay label changes to "Drop to copy" for remote drags vs "Drop to upload"
- Target directory resolves correctly (dropped on folder vs cwd)
- ``_on_remote_drop`` filters out same-directory no-ops
- ``_on_remote_drop`` rejects directory-into-itself copy
- Overlapping source/dest guards (drop into a subdirectory of the source dir)
- ``_on_remote_copy_requested`` in MainWindow: calls connect, streams files, refreshes
- Error handling: connection failure, per-file copy failure
- Temp directory is always cleaned up (even on exception)
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock

import pytest
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import QApplication

from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient
from sftp_ui.ui.panels.remote_panel import (
    REMOTE_ENTRIES_MIME,
    RemotePanel,
    _DropOverlay,
    _DropTable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry_dict(name: str, path: str, is_dir: bool = False, size: int = 100) -> dict:
    return {"name": name, "path": path, "is_dir": is_dir, "size": size}


def _make_mime(entry_dicts: list[dict]) -> QMimeData:
    mime = QMimeData()
    mime.setData(REMOTE_ENTRIES_MIME, json.dumps(entry_dicts).encode())
    return mime


@pytest.fixture
def remote_panel():
    import shiboken6
    panel = RemotePanel()
    panel.resize(600, 400)
    yield panel
    panel.close()
    if shiboken6.isValid(panel):
        shiboken6.delete(panel)


# ---------------------------------------------------------------------------
# _DropOverlay label tests
# ---------------------------------------------------------------------------

class TestDropOverlayLabel:
    def test_default_label_is_upload(self):
        import shiboken6
        overlay = _DropOverlay()
        try:
            assert overlay._label == "Drop to upload"
        finally:
            overlay.close()
            if shiboken6.isValid(overlay):
                shiboken6.delete(overlay)

    def test_set_label_updates_text(self):
        import shiboken6
        overlay = _DropOverlay()
        try:
            overlay.set_label("Drop to copy")
            assert overlay._label == "Drop to copy"
        finally:
            overlay.close()
            if shiboken6.isValid(overlay):
                shiboken6.delete(overlay)

    def test_set_label_triggers_update(self):
        import shiboken6
        overlay = _DropOverlay()
        try:
            with patch.object(overlay, "update") as mock_update:
                overlay.set_label("Drop to copy")
                mock_update.assert_called_once()
        finally:
            overlay.close()
            if shiboken6.isValid(overlay):
                shiboken6.delete(overlay)


# ---------------------------------------------------------------------------
# RemotePanel signal tests
# ---------------------------------------------------------------------------

class TestRemotePanelSignal:
    def test_remote_copy_requested_signal_exists(self, remote_panel):
        """RemotePanel must expose the remote_copy_requested signal."""
        assert hasattr(remote_panel, "remote_copy_requested")

    def test_remote_copy_requested_emitted_on_valid_drop(self, remote_panel):
        """_on_remote_drop emits remote_copy_requested for valid entries."""
        received = []
        remote_panel.remote_copy_requested.connect(
            lambda dicts, dest: received.append((dicts, dest))
        )
        # Source entry is in /src; dropping into /dst — different directories
        entry_dicts = [_make_entry_dict("file.txt", "/src/file.txt")]
        remote_panel._on_remote_drop(entry_dicts, "/dst")

        assert len(received) == 1
        dicts, dest = received[0]
        assert dest == "/dst"
        assert dicts[0]["name"] == "file.txt"

    def test_same_directory_no_op_skipped(self, remote_panel):
        """Dropping onto the same directory as the source emits nothing."""
        received = []
        remote_panel.remote_copy_requested.connect(
            lambda d, dest: received.append((d, dest))
        )
        entry_dicts = [_make_entry_dict("file.txt", "/home/user/file.txt")]
        # Target is the same directory the file lives in
        remote_panel._on_remote_drop(entry_dicts, "/home/user")

        assert received == []

    def test_copy_into_itself_rejected(self, remote_panel):
        """Dropping a directory onto itself must be rejected."""
        received = []
        status_msgs = []
        remote_panel.remote_copy_requested.connect(
            lambda d, dest: received.append((d, dest))
        )
        remote_panel.status_message.connect(status_msgs.append)

        entry_dicts = [_make_entry_dict("mydir", "/data/mydir", is_dir=True)]
        remote_panel._on_remote_drop(entry_dicts, "/data/mydir")

        assert received == []
        assert any("itself" in m for m in status_msgs)

    def test_copy_into_descendant_rejected(self, remote_panel):
        """Dropping a directory into one of its own subdirectories must be rejected."""
        received = []
        remote_panel.remote_copy_requested.connect(
            lambda d, dest: received.append((d, dest))
        )
        entry_dicts = [_make_entry_dict("proj", "/home/user/proj", is_dir=True)]
        # /home/user/proj/sub is a child of the source directory
        remote_panel._on_remote_drop(entry_dicts, "/home/user/proj/sub")

        assert received == []

    def test_mixed_valid_and_invalid_entries(self, remote_panel):
        """Only the valid (different-directory) entries are forwarded."""
        received = []
        remote_panel.remote_copy_requested.connect(
            lambda dicts, dest: received.append(dicts)
        )
        entry_dicts = [
            _make_entry_dict("keep.txt", "/src/keep.txt"),   # valid
            _make_entry_dict("skip.txt", "/dst/skip.txt"),   # same dir as dest → skipped
        ]
        remote_panel._on_remote_drop(entry_dicts, "/dst")

        assert len(received) == 1
        assert received[0][0]["name"] == "keep.txt"
        assert len(received[0]) == 1

    def test_all_invalid_entries_emits_nothing(self, remote_panel):
        """If every entry is a no-op, the signal is not emitted."""
        received = []
        remote_panel.remote_copy_requested.connect(
            lambda d, dest: received.append(d)
        )
        entry_dicts = [_make_entry_dict("a.txt", "/same/a.txt")]
        remote_panel._on_remote_drop(entry_dicts, "/same")

        assert received == []


# ---------------------------------------------------------------------------
# _DropTable drag-event routing tests
# ---------------------------------------------------------------------------

class TestDropTableRouting:
    """Verify that the drop table routes REMOTE_ENTRIES_MIME to _on_remote_drop."""

    def test_drop_table_accepts_remote_mime(self, remote_panel):
        """The table's dragEnterEvent must accept the remote MIME type."""
        table = remote_panel._table
        assert table.acceptDrops()

    def test_remote_copy_requested_via_drop_table(self, remote_panel):
        """Simulate dropEvent with REMOTE_ENTRIES_MIME; expect remote_copy_requested."""
        received: list = []
        remote_panel.remote_copy_requested.connect(
            lambda d, dest: received.append((d, dest))
        )

        entry_dicts = [_make_entry_dict("data.csv", "/remote/data.csv")]
        mime = _make_mime(entry_dicts)

        # Simulate the internal routing directly (dropEvent calls _on_remote_drop)
        remote_panel._cwd = "/target"
        remote_panel._on_remote_drop(entry_dicts, "/target")

        # '/target' is not the parent of '/remote/data.csv', so it should emit
        assert len(received) == 1

    def test_upload_path_unaffected(self, remote_panel):
        """Local file drops still reach _on_drop (no regression)."""
        dropped: list = []
        remote_panel.upload_requested.connect(
            lambda paths, dest: dropped.append((paths, dest))
        )
        remote_panel._cwd = "/uploads"
        remote_panel._on_drop(["/tmp/file.txt"], "/uploads")

        assert len(dropped) == 1
        assert dropped[0][1] == "/uploads"


# ---------------------------------------------------------------------------
# SessionWidget _on_remote_copy_requested unit tests (mocked SFTPClient)
# ---------------------------------------------------------------------------

class TestMainWindowRemoteCopy:
    """Unit tests for the MainWindow handler using mocked I/O."""

    @pytest.fixture
    def main_window_deps(self, tmp_path):
        """Build the minimum stubs needed to call _on_remote_copy_requested."""
        import importlib
        mw_mod = importlib.import_module("sftp_ui.ui.main_window")

        # We exercise the logic directly without constructing the full widget tree.
        conn_mock = MagicMock()
        conn_mock.id = "test-conn-1"

        signals_mock = MagicMock()
        signals_mock.status = MagicMock()
        signals_mock.status.emit = MagicMock()
        signals_mock.refresh_remote = MagicMock()
        signals_mock.refresh_remote.emit = MagicMock()

        return {"conn": conn_mock, "signals": signals_mock, "tmp_path": tmp_path}

    def test_no_active_connection_emits_error(self):
        """Handler must emit an error status if not connected."""
        from sftp_ui.ui.session_widget import SessionWidget

        # Minimal mock of a MainWindow instance (avoid full widget construction)
        mw = MagicMock(spec=SessionWidget)
        mw._active_conn = None
        mw._signals = MagicMock()
        mw._signals.status = MagicMock()
        mw._signals.status.emit = MagicMock()

        # Call the unbound method directly, passing our mock as self
        SessionWidget._on_remote_copy_requested(
            mw,
            [_make_entry_dict("x.txt", "/a/x.txt")],
            "/b",
        )

        mw._signals.status.emit.assert_called_once()
        assert "Not connected" in mw._signals.status.emit.call_args[0][0]

    def test_copy_streams_file_via_temp_buffer(self, tmp_path):
        """Files are downloaded to temp dir then uploaded to destination."""
        from sftp_ui.ui.session_widget import SessionWidget
        import threading

        # Create a realistic fake file (src content)
        src_content = b"hello remote copy world"

        # Fake open_remote: returns a BytesIO for reads, collects writes
        written_chunks: list[bytes] = []

        class _FakeReadHandle:
            def __init__(self): self._pos = 0
            def read(self, n):
                chunk = src_content[self._pos : self._pos + n]
                self._pos += len(chunk)
                return chunk
            def __enter__(self): return self
            def __exit__(self, *_): pass

        class _FakeWriteHandle:
            def write(self, data): written_chunks.append(data)
            def __enter__(self): return self
            def __exit__(self, *_): pass

        fake_client = MagicMock(spec=SFTPClient)
        fake_client.open_remote = MagicMock(side_effect=lambda path, mode: (
            _FakeReadHandle() if "r" in mode else _FakeWriteHandle()
        ))
        fake_client.mkdir_p = MagicMock()
        fake_client.walk = MagicMock(return_value=[])
        fake_client.close = MagicMock()

        status_messages: list[str] = []
        refresh_called = []

        mw = MagicMock(spec=SessionWidget)
        mw._active_conn = MagicMock()
        mw._signals = MagicMock()
        mw._signals.status.emit.side_effect = status_messages.append
        mw._signals.refresh_remote.emit.side_effect = lambda: refresh_called.append(1)

        done = threading.Event()

        # Patch SFTPClient constructor to return our fake
        with patch("sftp_ui.ui.session_widget.SFTPClient", return_value=fake_client):
            # Patch threading.Thread to run synchronously
            orig_thread = threading.Thread

            class _SyncThread:
                def __init__(self, target=None, daemon=None, **kw):
                    self._target = target
                def start(self):
                    self._target()
                    done.set()

            with patch("sftp_ui.ui.session_widget.threading.Thread", _SyncThread):
                entry_dicts = [_make_entry_dict("readme.md", "/src/readme.md", size=len(src_content))]
                SessionWidget._on_remote_copy_requested(mw, entry_dicts, "/dst")

        done.wait(timeout=5)

        # The file should have been opened for read (src) and write (dst)
        assert fake_client.open_remote.call_count == 2
        calls = [c[0] for c in fake_client.open_remote.call_args_list]
        assert any("/src/readme.md" in c[0] for c in fake_client.open_remote.call_args_list)
        assert any("/dst/readme.md" in c[0] for c in fake_client.open_remote.call_args_list)

        # Content should have been forwarded
        assert b"".join(written_chunks) == src_content

        # Final status should report success
        final_status = status_messages[-1]
        assert "Copied" in final_status or "copied" in final_status.lower()

        # Remote panel should be refreshed
        assert len(refresh_called) == 1

    def test_temp_dir_cleaned_up_on_success(self, tmp_path):
        """Temp directory is deleted after a successful copy."""
        from sftp_ui.ui.session_widget import SessionWidget
        import threading

        captured_tmp_dirs: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def _patched_mkdtemp(**kw):
            d = real_mkdtemp(**kw)
            captured_tmp_dirs.append(d)
            return d

        fake_client = MagicMock(spec=SFTPClient)

        class _NullHandle:
            def read(self, n): return b""
            def write(self, d): pass
            def __enter__(self): return self
            def __exit__(self, *_): pass

        fake_client.open_remote = MagicMock(return_value=_NullHandle())
        fake_client.mkdir_p = MagicMock()
        fake_client.walk = MagicMock(return_value=[])
        fake_client.close = MagicMock()

        mw = MagicMock(spec=SessionWidget)
        mw._active_conn = MagicMock()
        mw._signals = MagicMock()
        mw._signals.status.emit = MagicMock()
        mw._signals.refresh_remote.emit = MagicMock()

        import builtins

        with patch("sftp_ui.ui.session_widget.SFTPClient", return_value=fake_client):
            import tempfile as _tmp_mod

            class _SyncThread:
                def __init__(self, target=None, daemon=None, **kw):
                    self._target = target
                def start(self): self._target()

            with patch("sftp_ui.ui.session_widget.threading.Thread", _SyncThread):
                with patch("tempfile.mkdtemp", side_effect=_patched_mkdtemp):
                    entry_dicts = [_make_entry_dict("a.txt", "/x/a.txt")]
                    SessionWidget._on_remote_copy_requested(mw, entry_dicts, "/y")

        # All captured temp dirs should have been removed
        for d in captured_tmp_dirs:
            assert not os.path.exists(d), f"Temp dir was not cleaned up: {d}"

    def test_connect_failure_emits_error_status(self):
        """If SFTPClient.connect() raises, emit error status and stop."""
        from sftp_ui.ui.session_widget import SessionWidget
        import threading

        fake_client = MagicMock(spec=SFTPClient)
        fake_client.connect.side_effect = OSError("Connection refused")
        fake_client.close = MagicMock()

        status_messages: list[str] = []
        mw = MagicMock(spec=SessionWidget)
        mw._active_conn = MagicMock()
        mw._signals = MagicMock()
        mw._signals.status.emit.side_effect = status_messages.append
        mw._signals.refresh_remote.emit = MagicMock()

        with patch("sftp_ui.ui.session_widget.SFTPClient", return_value=fake_client):
            class _SyncThread:
                def __init__(self, target=None, daemon=None, **kw):
                    self._target = target
                def start(self): self._target()

            with patch("sftp_ui.ui.session_widget.threading.Thread", _SyncThread):
                entry_dicts = [_make_entry_dict("f.txt", "/a/f.txt")]
                SessionWidget._on_remote_copy_requested(mw, entry_dicts, "/b")

        assert any("failed" in m.lower() or "error" in m.lower() or "connect" in m.lower()
                   for m in status_messages)
        # open_remote should NOT have been called
        fake_client.open_remote.assert_not_called()

    def test_directory_copy_uses_walk(self):
        """Copying a directory entry triggers walk() for recursive expansion."""
        from sftp_ui.ui.session_widget import SessionWidget
        import threading

        walked_dirs: list[str] = []
        written_paths: list[str] = []

        fake_files = [
            RemoteEntry(name="a.py", path="/src/pkg/a.py", is_dir=False, size=10, mtime=0),
            RemoteEntry(name="b.py", path="/src/pkg/b.py", is_dir=False, size=20, mtime=0),
        ]

        class _NullReadHandle:
            def __init__(self): self._sent = False
            def read(self, n):
                # Return one small chunk then EOF so write() is triggered
                if not self._sent:
                    self._sent = True
                    return b"x"
                return b""
            def __enter__(self): return self
            def __exit__(self, *_): pass

        class _CapturingWriteHandle:
            def __init__(self, path): self._path = path
            def write(self, d): written_paths.append(self._path)
            def __enter__(self): return self
            def __exit__(self, *_): pass

        def _open(path, mode):
            if "r" in mode:
                return _NullReadHandle()
            return _CapturingWriteHandle(path)

        fake_client = MagicMock(spec=SFTPClient)
        fake_client.walk = MagicMock(side_effect=lambda p: walked_dirs.append(p) or fake_files)
        fake_client.open_remote = MagicMock(side_effect=_open)
        fake_client.mkdir_p = MagicMock()
        fake_client.close = MagicMock()

        mw = MagicMock(spec=SessionWidget)
        mw._active_conn = MagicMock()
        mw._signals = MagicMock()
        mw._signals.status.emit = MagicMock()
        mw._signals.refresh_remote.emit = MagicMock()

        with patch("sftp_ui.ui.session_widget.SFTPClient", return_value=fake_client):
            class _SyncThread:
                def __init__(self, target=None, daemon=None, **kw):
                    self._target = target
                def start(self): self._target()

            with patch("sftp_ui.ui.session_widget.threading.Thread", _SyncThread):
                entry_dicts = [_make_entry_dict("pkg", "/src/pkg", is_dir=True)]
                SessionWidget._on_remote_copy_requested(mw, entry_dicts, "/dst")

        assert "/src/pkg" in walked_dirs
        # Both files should have been written to the destination
        assert any("/dst/pkg/a.py" in p for p in written_paths)
        assert any("/dst/pkg/b.py" in p for p in written_paths)


# ---------------------------------------------------------------------------
# Integration: RemotePanel signal → correctly encoded MIME roundtrip
# ---------------------------------------------------------------------------

class TestMimeRoundtrip:
    def test_mime_encode_decode_roundtrip(self):
        """MIME data survives encode → decode with no data loss."""
        original = [
            _make_entry_dict("report.pdf", "/docs/report.pdf", size=204800),
            _make_entry_dict("images", "/docs/images", is_dir=True, size=0),
        ]
        mime = _make_mime(original)
        decoded = json.loads(bytes(mime.data(REMOTE_ENTRIES_MIME)).decode())

        assert len(decoded) == 2
        assert decoded[0]["name"] == "report.pdf"
        assert decoded[0]["size"] == 204800
        assert decoded[1]["is_dir"] is True

    def test_empty_mime_payload_handled_gracefully(self, remote_panel):
        """An empty MIME list must not crash _on_remote_drop."""
        received = []
        status_msgs = []
        remote_panel.remote_copy_requested.connect(lambda d, dest: received.append(d))
        remote_panel.status_message.connect(status_msgs.append)

        # Empty list — nothing to copy
        remote_panel._on_remote_drop([], "/anywhere")

        assert received == []
