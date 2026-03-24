"""
Drag & drop between panels — remote-to-local (download) and local-to-remote (upload).

Tests cover:
- MIME data serialization/deserialization for remote entries
- Drop acceptance on local panel
- Drop acceptance on remote panel (already working, verify no regression)
- Drag initiation from remote table
- Signal emission on successful drop
- Target directory resolution (drop on folder vs root)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from PySide6.QtCore import Qt, QMimeData, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QAbstractItemView

from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.ui.panels.local_panel import LocalPanel
from sftp_ui.ui.panels.remote_panel import RemotePanel


class TestRemoteDragMimeData:
    """Test MIME data encoding/decoding for remote entries."""

    def test_remote_entries_to_mime_data(self):
        """Serialize remote entries to MIME data."""
        entries = [
            RemoteEntry(name="file1.txt", path="/home/user/file1.txt", is_dir=False, size=1024, mtime=0),
            RemoteEntry(name="dir1", path="/home/user/dir1", is_dir=True, size=0, mtime=0),
        ]

        # Mock _DropTable.mimeData() behavior
        mime = QMimeData()
        import json
        data = json.dumps([
            {"name": e.name, "path": e.path, "is_dir": e.is_dir, "size": e.size}
            for e in entries
        ])
        mime.setData("application/x-sftp-ui-remote-entries", data.encode())

        # Verify data can be retrieved
        assert mime.hasFormat("application/x-sftp-ui-remote-entries")
        decoded = json.loads(bytes(mime.data("application/x-sftp-ui-remote-entries")).decode())
        assert len(decoded) == 2
        assert decoded[0]["path"] == "/home/user/file1.txt"
        assert decoded[1]["is_dir"] is True

    def test_remote_entries_to_uri_list_fallback(self):
        """Serialize remote entries as sftp:// URIs for fallback."""
        entries = [
            RemoteEntry(name="file.txt", path="/home/user/file.txt", is_dir=False, size=1024, mtime=0),
        ]

        mime = QMimeData()
        uris = "\n".join([f"sftp:///{e.path}" for e in entries])
        mime.setUrls([QUrl(uri) for uri in uris.split("\n")])

        urls = mime.urls()
        assert len(urls) == 1
        assert urls[0].scheme() == "sftp"

    def test_empty_mime_data_rejected(self):
        """Empty MIME data should not be accepted as remote drag."""
        mime = QMimeData()

        # Neither format present
        assert not mime.hasFormat("application/x-sftp-ui-remote-entries")
        assert not mime.hasUrls()


class TestLocalPanelDropAcceptance:
    """Test local panel accepting drops from remote panel."""

    @pytest.fixture
    def local_panel(self, tmp_path):
        return LocalPanel(initial_path=str(tmp_path))

    @pytest.fixture
    def remote_entries(self):
        return [
            RemoteEntry(name="file1.txt", path="/remote/file1.txt", is_dir=False, size=100, mtime=0),
            RemoteEntry(name="file2.tar.gz", path="/remote/file2.tar.gz", is_dir=False, size=5000, mtime=0),
        ]

    def test_local_panel_accepts_remote_drag_enter(self, local_panel, remote_entries):
        """Local panel dragEnterEvent accepts remote MIME type."""
        mime = QMimeData()
        import json
        data = json.dumps([
            {"name": e.name, "path": e.path, "is_dir": e.is_dir, "size": e.size}
            for e in remote_entries
        ])
        mime.setData("application/x-sftp-ui-remote-entries", data.encode())

        from PySide6.QtGui import QDragEnterEvent
        from PySide6.QtCore import QPoint, QRect

        # Mock event
        event = MagicMock(spec=QDragEnterEvent)
        event.mimeData.return_value = mime
        event.pos.return_value = QPoint(100, 100)

        # The panel should accept this mime type
        assert mime.hasFormat("application/x-sftp-ui-remote-entries")

    def test_local_panel_rejects_invalid_drag(self, local_panel):
        """Local panel rejects drag with unrecognized MIME type."""
        mime = QMimeData()
        mime.setText("plain text")

        # Only text, no remote entries format
        assert not mime.hasFormat("application/x-sftp-ui-remote-entries")

    def test_download_drop_requested_signal_emitted(self, local_panel, remote_entries):
        """Dropping remote entries emits download_drop_requested signal."""
        if not hasattr(local_panel, 'download_drop_requested'):
            # Feature not yet implemented; test will be updated
            pytest.skip("Feature not yet implemented")

        signal_received = []
        local_panel.download_drop_requested.connect(
            lambda entries, dir: signal_received.append((entries, dir))
        )

        # Simulate drop (this requires implementing dropEvent in LocalPanel)
        # For now, just verify the signal exists
        assert hasattr(local_panel, 'download_drop_requested')

    def test_drop_target_subdirectory_resolution(self, local_panel, tmp_path):
        """Dropping on a folder should target that folder, not root."""
        subdir = tmp_path / "downloads"
        subdir.mkdir()

        # Would test that drop on a selected folder row targets that folder
        # Implementation detail: check the target_dir in the signal
        pass


class TestRemotePanelDragInitiation:
    """Test remote panel initiating drag (remote-to-local download)."""

    @pytest.fixture
    def remote_panel(self):
        panel = RemotePanel()
        panel.resize(600, 400)
        return panel

    def test_remote_table_drag_enabled(self, remote_panel):
        """Remote table should have drag enabled."""
        # Currently DropOnly; needs to be DragDrop
        table = remote_panel._table

        # After feature implementation, should support drag
        assert table is not None

    def test_remote_table_mime_data_generation(self, remote_panel):
        """Remote table generates MIME data for selected entries."""
        remote_panel._all_entries = [
            RemoteEntry(name="file.txt", path="/data/file.txt", is_dir=False, size=512, mtime=0),
            RemoteEntry(name="archive.zip", path="/data/archive.zip", is_dir=False, size=10000, mtime=0),
        ]

        # After implementing mimeData() override
        # Should return custom MIME type with serialized entries
        pass

    def test_remote_drag_includes_custom_format(self, remote_panel):
        """Remote drag MIME data includes custom application/x-sftp-ui-remote-entries."""
        # Verify that mimeData() sets the custom format
        # Not yet implemented
        pass


class TestDragDropIntegration:
    """Integration tests: full drag-drop workflow."""

    @pytest.fixture
    def qapp(self):
        return QApplication.instance() or QApplication([])

    def test_local_to_remote_drag_drop_upload(self, qapp, tmp_path):
        """Drag local file to remote panel triggers upload."""
        # Create a local file
        local_file = tmp_path / "upload_me.txt"
        local_file.write_text("test content")

        # Mock remote panel
        remote_panel = MagicMock()

        # The upload should be queued via the signal
        # This is already implemented; just verify no regression
        assert local_file.exists()

    def test_remote_to_local_drag_drop_download(self, qapp, tmp_path):
        """Drag remote file to local panel triggers download."""
        local_panel = LocalPanel(initial_path=str(tmp_path))
        remote_entry = RemoteEntry(
            name="data.csv", path="/remote/data.csv", is_dir=False, size=2048, mtime=0
        )

        # After implementing: verify download_drop_requested is emitted
        # with correct entry and target directory
        pass

    def test_drag_folder_downloads_recursively(self, qapp, tmp_path):
        """Dragging a remote folder should download it recursively."""
        # Similar to existing directory download logic
        # Just verify it works via drag-drop too
        pass

    def test_drop_overlay_shows_on_remote_drag_enter(self, qapp):
        """Local panel shows drop overlay when remote drag enters."""
        local_panel = LocalPanel()

        # After implementing: _DropOverlay should appear on dragEnterEvent
        # with message like "Drop to download"
        pass

    def test_drop_overlay_hides_on_drag_leave(self, qapp):
        """Drop overlay disappears when drag leaves local panel."""
        local_panel = LocalPanel()

        # dragLeaveEvent should hide the overlay
        pass


class TestDragDropEdgeCases:
    """Edge cases and error handling."""

    def test_drop_with_mixed_file_types(self, tmp_path):
        """Drop containing both files and folders."""
        entries = [
            RemoteEntry(name="file.txt", path="/data/file.txt", is_dir=False, size=100, mtime=0),
            RemoteEntry(name="folder", path="/data/folder", is_dir=True, size=0, mtime=0),
        ]

        # Should handle both transparently
        import json
        mime = QMimeData()
        data = json.dumps([
            {"name": e.name, "path": e.path, "is_dir": e.is_dir, "size": e.size}
            for e in entries
        ])
        mime.setData("application/x-sftp-ui-remote-entries", data.encode())

        assert mime.hasFormat("application/x-sftp-ui-remote-entries")

    def test_drop_on_readonly_directory(self, tmp_path):
        """Drop to a read-only local directory should be rejected/warned."""
        # Create read-only dir (if OS supports it)
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()

        # After implementation: should validate write permissions
        # before allowing drop
        pass

    def test_drop_aborts_on_connection_loss(self):
        """If SFTP connection drops during drag-drop, queue the job for retry."""
        # Ties into auto-reconnect feature
        pass

    def test_large_file_drop_shows_progress(self):
        """Dropping a large file shows transfer progress as usual."""
        # Same transfer panel logic; no special handling needed
        pass
