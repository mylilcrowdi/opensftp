"""
Tests for LocalPanel filesystem operations.

Covers: _do_new_folder, _do_new_file, _do_paste, _do_rename, _do_delete.
QInputDialog and QMessageBox are patched so no modal dialog blocks the test.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from sftp_ui.ui.panels.local_panel import LocalPanel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _panel(tmp_path) -> LocalPanel:
    return LocalPanel(initial_path=str(tmp_path))


# ── _do_new_folder ─────────────────────────────────────────────────────────────

class TestDoNewFolder:
    def test_creates_folder_on_disk(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("new_dir", True)):
            panel._do_new_folder()
        assert (tmp_path / "new_dir").is_dir()

    def test_folder_appears_in_tree(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("my_folder", True)):
            panel._do_new_folder()
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert any("my_folder" in n for n in names)

    def test_cancel_dialog_creates_nothing(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        before = set(os.listdir(tmp_path))
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("ignored", False)):  # ok=False → cancel
            panel._do_new_folder()
        assert set(os.listdir(tmp_path)) == before

    def test_empty_name_creates_nothing(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        before = set(os.listdir(tmp_path))
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("   ", True)):  # whitespace only
            panel._do_new_folder()
        assert set(os.listdir(tmp_path)) == before

    def test_oserror_shows_warning_not_crash(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("x", True)), \
             patch("os.mkdir", side_effect=OSError("no space")), \
             patch("sftp_ui.ui.panels.local_panel.QMessageBox.warning") as warn:
            panel._do_new_folder()
        warn.assert_called_once()

    def test_whitespace_stripped_from_name(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("  trimmed  ", True)):
            panel._do_new_folder()
        assert (tmp_path / "trimmed").is_dir()


# ── _do_new_file ───────────────────────────────────────────────────────────────

class TestDoNewFile:
    def test_creates_empty_file_on_disk(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("hello.txt", True)):
            panel._do_new_file()
        p = tmp_path / "hello.txt"
        assert p.exists()
        assert p.stat().st_size == 0

    def test_file_appears_in_tree(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("readme.md", True)):
            panel._do_new_file()
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert any("readme.md" in n for n in names)

    def test_cancel_creates_nothing(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        before = set(os.listdir(tmp_path))
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("never.txt", False)):
            panel._do_new_file()
        assert set(os.listdir(tmp_path)) == before

    def test_empty_name_creates_nothing(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        before = set(os.listdir(tmp_path))
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("", True)):
            panel._do_new_file()
        assert set(os.listdir(tmp_path)) == before

    def test_oserror_shows_warning(self, qapp, tmp_path):
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("f.txt", True)), \
             patch("builtins.open", side_effect=OSError("disk full")), \
             patch("sftp_ui.ui.panels.local_panel.QMessageBox.warning") as warn:
            panel._do_new_file()
        warn.assert_called_once()


# ── _do_rename ─────────────────────────────────────────────────────────────────

class TestDoRename:
    def test_rename_file_on_disk(self, qapp, tmp_path):
        src = tmp_path / "old.txt"
        src.write_text("content")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("new.txt", True)):
            panel._do_rename(str(src), is_dir=False)
        assert (tmp_path / "new.txt").exists()
        assert not src.exists()

    def test_rename_content_preserved(self, qapp, tmp_path):
        src = tmp_path / "data.bin"
        src.write_bytes(b"hello world")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("data2.bin", True)):
            panel._do_rename(str(src), is_dir=False)
        assert (tmp_path / "data2.bin").read_bytes() == b"hello world"

    def test_rename_directory(self, qapp, tmp_path):
        d = tmp_path / "olddir"
        d.mkdir()
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("newdir", True)):
            panel._do_rename(str(d), is_dir=True)
        assert (tmp_path / "newdir").is_dir()
        assert not d.exists()

    def test_rename_cancel_leaves_file(self, qapp, tmp_path):
        src = tmp_path / "keep.txt"
        src.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("other.txt", False)):
            panel._do_rename(str(src), is_dir=False)
        assert src.exists()

    def test_rename_same_name_no_op(self, qapp, tmp_path):
        src = tmp_path / "same.txt"
        src.write_text("x")
        panel = _panel(tmp_path)
        before = set(os.listdir(tmp_path))
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("same.txt", True)):  # same name → skip
            panel._do_rename(str(src), is_dir=False)
        assert set(os.listdir(tmp_path)) == before

    def test_rename_oserror_shows_warning(self, qapp, tmp_path):
        src = tmp_path / "f.txt"
        src.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("g.txt", True)), \
             patch("os.rename", side_effect=OSError("locked")), \
             patch("sftp_ui.ui.panels.local_panel.QMessageBox.warning") as warn:
            panel._do_rename(str(src), is_dir=False)
        warn.assert_called_once()

    def test_renamed_file_appears_in_tree(self, qapp, tmp_path):
        src = tmp_path / "before.txt"
        src.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("after.txt", True)):
            panel._do_rename(str(src), is_dir=False)
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert any("after.txt" in n for n in names)
        assert not any("before.txt" in n for n in names)


# ── _do_delete ─────────────────────────────────────────────────────────────────

class TestDoDelete:
    def test_delete_file_confirmed(self, qapp, tmp_path):
        f = tmp_path / "bye.txt"
        f.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            panel._do_delete(str(f), is_dir=False)
        assert not f.exists()

    def test_delete_file_cancelled(self, qapp, tmp_path):
        f = tmp_path / "keep.txt"
        f.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No):
            panel._do_delete(str(f), is_dir=False)
        assert f.exists()

    def test_delete_directory_confirmed(self, qapp, tmp_path):
        d = tmp_path / "delme"
        d.mkdir()
        (d / "file.txt").write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            panel._do_delete(str(d), is_dir=True)
        assert not d.exists()

    def test_delete_directory_cancelled_keeps_tree(self, qapp, tmp_path):
        d = tmp_path / "keep_dir"
        d.mkdir()
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No):
            panel._do_delete(str(d), is_dir=True)
        assert d.is_dir()

    def test_delete_refreshes_tree(self, qapp, tmp_path):
        f = tmp_path / "gone.txt"
        f.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            panel._do_delete(str(f), is_dir=False)
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert not any("gone.txt" in n for n in names)

    def test_delete_oserror_shows_warning(self, qapp, tmp_path):
        f = tmp_path / "stuck.txt"
        f.write_text("x")
        panel = _panel(tmp_path)
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes), \
             patch("os.remove", side_effect=OSError("busy")), \
             patch("sftp_ui.ui.panels.local_panel.QMessageBox.warning") as warn:
            panel._do_delete(str(f), is_dir=False)
        warn.assert_called_once()


# ── _do_paste ──────────────────────────────────────────────────────────────────

class TestDoPaste:
    def test_paste_single_file(self, qapp, tmp_path):
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        src_file = src_dir / "copy_me.txt"
        src_file.write_text("data")

        panel = LocalPanel(initial_path=str(dst_dir))
        panel._do_paste([str(src_file)])

        assert (dst_dir / "copy_me.txt").exists()
        assert (dst_dir / "copy_me.txt").read_text() == "data"

    def test_paste_multiple_files(self, qapp, tmp_path):
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        for name in ("a.txt", "b.txt", "c.txt"):
            (src_dir / name).write_text(name)

        panel = LocalPanel(initial_path=str(dst_dir))
        panel._do_paste([str(src_dir / n) for n in ("a.txt", "b.txt", "c.txt")])

        for name in ("a.txt", "b.txt", "c.txt"):
            assert (dst_dir / name).exists()

    def test_paste_directory(self, qapp, tmp_path):
        src_root = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_root.mkdir()
        dst_dir.mkdir()
        src_sub = src_root / "subdir"
        src_sub.mkdir()
        (src_sub / "nested.txt").write_text("nested")

        panel = LocalPanel(initial_path=str(dst_dir))
        panel._do_paste([str(src_sub)])

        assert (dst_dir / "subdir" / "nested.txt").read_text() == "nested"

    def test_paste_refreshes_tree(self, qapp, tmp_path):
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "visible.txt").write_text("x")

        panel = LocalPanel(initial_path=str(dst_dir))
        panel._do_paste([str(src_dir / "visible.txt")])

        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert any("visible.txt" in n for n in names)

    def test_paste_oserror_shows_warning(self, qapp, tmp_path):
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        src_file = src_dir / "f.txt"
        src_file.write_text("x")

        panel = LocalPanel(initial_path=str(dst_dir))
        with patch("shutil.copy2", side_effect=OSError("disk full")), \
             patch("sftp_ui.ui.panels.local_panel.QMessageBox.warning") as warn:
            panel._do_paste([str(src_file)])
        warn.assert_called_once()
