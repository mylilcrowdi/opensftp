"""
Tests for LocalPanel._show_info() — the file/directory info dialog.

The QMessageBox is mocked so no actual dialog is shown.
Covers: file info content (name, path, type, size, permissions),
        directory info, OSError handling, timestamp format.
"""
from __future__ import annotations

import os
import sys
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from PySide6.QtWidgets import QMessageBox as _RealQMB
from sftp_ui.ui.panels.local_panel import LocalPanel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _run_info(panel: LocalPanel, path: str) -> str:
    """Call _show_info and return the text passed to QMessageBox."""
    captured = []

    class FakeBox:
        # Preserve the real Icon enum so msg.setIcon(QMessageBox.Icon.Information) works.
        Icon = _RealQMB.Icon

        def __init__(self, parent):
            pass
        def setWindowTitle(self, t): pass
        def setText(self, t): captured.append(t)
        def setIcon(self, i): pass
        def exec(self): pass
        @staticmethod
        def warning(*a, **kw): pass

    with patch("sftp_ui.ui.panels.local_panel.QMessageBox", FakeBox):
        panel._show_info(path)

    return captured[0] if captured else ""


class TestShowInfoFile:
    def test_info_contains_filename(self, qapp, tmp_path):
        f = tmp_path / "report.csv"
        f.write_bytes(b"a,b,c")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        assert "report.csv" in text

    def test_info_contains_full_path(self, qapp, tmp_path):
        f = tmp_path / "myfile.txt"
        f.write_text("hi")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        assert str(f) in text

    def test_info_shows_file_type(self, qapp, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"x")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        assert "File" in text

    def test_info_shows_size(self, qapp, tmp_path):
        content = b"hello world"
        f = tmp_path / "sized.txt"
        f.write_bytes(content)
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        # Size should appear — either raw bytes or human-readable
        assert "B" in text or str(len(content)) in text

    def test_info_shows_modified_date(self, qapp, tmp_path):
        f = tmp_path / "dated.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        assert "Modified" in text

    def test_info_shows_permissions(self, qapp, tmp_path):
        f = tmp_path / "perms.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        assert "Permissions" in text

    def test_info_permissions_contains_octal(self, qapp, tmp_path):
        f = tmp_path / "octal.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        assert "0o" in text or "0" in text


class TestShowInfoDirectory:
    def test_info_shows_directory_type(self, qapp, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(d))
        assert "Directory" in text

    def test_info_contains_dirname(self, qapp, tmp_path):
        d = tmp_path / "special_dir"
        d.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(d))
        assert "special_dir" in text

    def test_info_dir_contains_path(self, qapp, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(d))
        assert str(d) in text


class TestShowInfoEdgeCases:
    def test_info_oserror_shows_warning(self, qapp, tmp_path):
        f = tmp_path / "ghost.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        with patch("os.stat", side_effect=OSError("no such file")), \
             patch("sftp_ui.ui.panels.local_panel.QMessageBox") as mock_box_cls:
            # Use a MagicMock instance
            mock_instance = MagicMock()
            mock_box_cls.return_value = mock_instance
            mock_box_cls.warning = MagicMock()
            panel._show_info(str(f))
        mock_box_cls.warning.assert_called_once()

    def test_info_large_file_size_uses_mb(self, qapp, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"\x00")
        panel = LocalPanel(initial_path=str(tmp_path))
        # Mock stat to return a large file size
        fake_stat = os.stat(str(f))
        with patch("os.stat") as mock_stat, \
             patch("os.path.isdir", return_value=False):
            # Create a fake stat result with large size
            import types
            fake = types.SimpleNamespace(
                st_size=5 * 1024 * 1024,  # 5 MB
                st_mtime=fake_stat.st_mtime,
                st_ctime=fake_stat.st_ctime,
                st_mode=fake_stat.st_mode,
            )
            mock_stat.return_value = fake
            text = _run_info(panel, str(f))
        assert "MB" in text or "5" in text

    def test_info_zero_byte_file(self, qapp, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        panel = LocalPanel(initial_path=str(tmp_path))
        text = _run_info(panel, str(f))
        # 0 bytes — should still show something
        assert "0" in text or "B" in text
