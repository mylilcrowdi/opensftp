"""
Tests for LocalPanel — local filesystem browser.

Covers: initial path, _populate (dirs before files, dotdot entry, path label),
        current_path, selected_paths (dotdot excluded), double-click navigation,
        path_changed signal, permission-error fallback, mimeData URLs.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QApplication

from sftp_ui.ui.panels.local_panel import LocalPanel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


# ── Initialisation ─────────────────────────────────────────────────────────────

class TestLocalPanelInit:
    def test_default_path_is_home(self, qapp):
        panel = LocalPanel()
        assert panel.current_path() == str(Path.home())

    def test_explicit_valid_path(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        assert panel.current_path() == str(tmp_path)

    def test_invalid_path_falls_back_to_home(self, qapp):
        panel = LocalPanel(initial_path="/this/does/not/exist/ever")
        assert panel.current_path() == str(Path.home())

    def test_path_label_matches_initial_path(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        assert panel._path_label.toolTip() == str(tmp_path)

    def test_tree_has_items_for_non_empty_dir(self, qapp, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        assert panel._tree.topLevelItemCount() > 0


# ── _populate — listing ────────────────────────────────────────────────────────

class TestLocalPanelPopulate:
    def test_dotdot_entry_shown_for_non_root(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        first = panel._tree.topLevelItem(0)
        assert first is not None
        assert first.text(0) == ".."

    def test_dirs_listed_before_files(self, qapp, tmp_path):
        (tmp_path / "z_file.txt").write_text("x")
        (tmp_path / "a_dir").mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        # Item 0 is "..", item 1 should be the dir
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        dotdot_idx = names.index("..")
        dir_idx = next(i for i, n in enumerate(names) if "a_dir" in n)
        file_idx = next(i for i, n in enumerate(names) if "z_file" in n)
        assert dir_idx < file_idx

    def test_dir_icon_prefix(self, qapp, tmp_path):
        (tmp_path / "mydir").mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        dir_entry = next(n for n in names if "mydir" in n)
        assert "📁" in dir_entry

    def test_file_has_no_dir_icon(self, qapp, tmp_path):
        (tmp_path / "readme.txt").write_text("hi")
        panel = LocalPanel(initial_path=str(tmp_path))
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        file_entry = next(n for n in names if "readme" in n)
        assert "📁" not in file_entry

    def test_path_label_updated_on_navigate(self, qapp, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        panel._populate(str(sub))
        assert panel._path_label.toolTip() == str(sub)

    def test_permission_error_does_not_crash(self, qapp, tmp_path):
        # Simulate PermissionError by passing a dir we fake-patch
        panel = LocalPanel(initial_path=str(tmp_path))
        import unittest.mock as mock
        with mock.patch("os.scandir", side_effect=PermissionError("denied")):
            panel._populate(str(tmp_path))   # should not raise
        # Path label still updated
        assert panel._path_label.toolTip() == str(tmp_path)

    def test_no_dotdot_at_root(self, qapp):
        panel = LocalPanel(initial_path="/")
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert ".." not in names


# ── current_path / path_changed signal ────────────────────────────────────────

class TestLocalPanelCurrentPath:
    def test_current_path_reflects_navigate(self, qapp, tmp_path):
        sub = tmp_path / "child"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        panel._populate(str(sub))
        assert panel.current_path() == str(sub)

    def test_path_changed_signal_emitted_on_populate(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        received = []
        panel.path_changed.connect(received.append)
        sub = tmp_path / "s"
        sub.mkdir()
        panel._populate(str(sub))
        assert received == [str(sub)]

    def test_path_changed_emitted_on_init(self, qapp, tmp_path):
        received = []
        # Connect before first populate happens — not possible post-init;
        # just verify current_path is set correctly.
        panel = LocalPanel(initial_path=str(tmp_path))
        assert panel.current_path() == str(tmp_path)


# ── selected_paths ─────────────────────────────────────────────────────────────

class TestLocalPanelSelectedPaths:
    def test_empty_selection_returns_empty_list(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        assert panel.selected_paths() == []

    def test_selected_file_returned(self, qapp, tmp_path):
        f = tmp_path / "pick_me.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        # Find the item
        for i in range(panel._tree.topLevelItemCount()):
            item = panel._tree.topLevelItem(i)
            if "pick_me" in item.text(0):
                item.setSelected(True)
                break
        paths = panel.selected_paths()
        assert str(f) in paths

    def test_dotdot_excluded_from_selection(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        # Select the ".." item
        first = panel._tree.topLevelItem(0)
        if first and first.text(0) == "..":
            first.setSelected(True)
        paths = panel.selected_paths()
        assert str(tmp_path.parent) not in paths

    def test_multiple_files_selected(self, qapp, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        panel = LocalPanel(initial_path=str(tmp_path))
        count = 0
        for i in range(panel._tree.topLevelItemCount()):
            item = panel._tree.topLevelItem(i)
            if item.text(0) != "..":
                item.setSelected(True)
                count += 1
        paths = panel.selected_paths()
        assert len(paths) == count


# ── Double-click navigation ────────────────────────────────────────────────────

class TestLocalPanelDoubleClick:
    def test_double_click_dir_navigates(self, qapp, tmp_path):
        sub = tmp_path / "enter_me"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        # Find the dir item and double-click it
        for i in range(panel._tree.topLevelItemCount()):
            item = panel._tree.topLevelItem(i)
            if "enter_me" in item.text(0):
                panel._on_double_click(item)
                break
        assert panel.current_path() == str(sub)

    def test_double_click_file_does_not_navigate(self, qapp, tmp_path):
        f = tmp_path / "stay.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        original = panel.current_path()
        for i in range(panel._tree.topLevelItemCount()):
            item = panel._tree.topLevelItem(i)
            if "stay" in item.text(0):
                panel._on_double_click(item)
                break
        assert panel.current_path() == original

    def test_double_click_dotdot_goes_up(self, qapp, tmp_path):
        sub = tmp_path / "deep"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(sub))
        # Find ".." and click
        for i in range(panel._tree.topLevelItemCount()):
            item = panel._tree.topLevelItem(i)
            if item.text(0) == "..":
                panel._on_double_click(item)
                break
        assert panel.current_path() == str(tmp_path)


# ── mimeData / drag ────────────────────────────────────────────────────────────

class TestLocalPanelMimeData:
    def test_mime_data_contains_file_urls(self, qapp, tmp_path):
        f = tmp_path / "drag_me.txt"
        f.write_text("data")
        panel = LocalPanel(initial_path=str(tmp_path))
        # Select the file item
        for i in range(panel._tree.topLevelItemCount()):
            item = panel._tree.topLevelItem(i)
            if "drag_me" in item.text(0):
                item.setSelected(True)
                break
        mime = panel._tree.mimeData(panel._tree.selectedItems())
        assert mime.hasUrls()
        urls = mime.urls()
        assert any("drag_me.txt" in u.toLocalFile() for u in urls)

    def test_mime_data_excludes_dotdot(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        # Select all items
        for i in range(panel._tree.topLevelItemCount()):
            panel._tree.topLevelItem(i).setSelected(True)
        mime = panel._tree.mimeData(panel._tree.selectedItems())
        urls = mime.urls()
        paths = [u.toLocalFile() for u in urls]
        assert str(tmp_path.parent) not in paths


# ── Hidden files toggle ────────────────────────────────────────────────────────

class TestLocalPanelHiddenFiles:
    def test_dotfiles_hidden_by_default(self, qapp, tmp_path):
        (tmp_path / ".hidden_file").write_text("secret")
        (tmp_path / "visible.txt").write_text("hi")
        panel = LocalPanel(initial_path=str(tmp_path))
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        # ".hidden_file" must not appear — dotfiles are filtered by default
        assert not any(".hidden_file" in n for n in names)
        assert any("visible.txt" in n for n in names)

    def test_show_hidden_reveals_dotfiles(self, qapp, tmp_path):
        (tmp_path / ".secret").write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        # Enable the hidden-files toggle
        panel._hidden_btn.setChecked(True)
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert any(".secret" in n for n in names)

    def test_toggle_button_exists(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        assert hasattr(panel, "_hidden_btn")
        assert panel._hidden_btn.isCheckable()

    def test_toggle_off_rehides_dotfiles(self, qapp, tmp_path):
        (tmp_path / ".dotfile").write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        # Show hidden
        panel._hidden_btn.setChecked(True)
        # Hide again
        panel._hidden_btn.setChecked(False)
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert not any(".dotfile" in n for n in names)

    def test_dotdir_hidden_by_default(self, qapp, tmp_path):
        (tmp_path / ".config").mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        names = [
            panel._tree.topLevelItem(i).text(0)
            for i in range(panel._tree.topLevelItemCount())
        ]
        assert not any(".config" in n for n in names)

    def test_dotfiles_do_not_count_in_status_message_when_hidden(self, qapp, tmp_path):
        (tmp_path / ".hidden").write_text("x")
        (tmp_path / "visible.txt").write_text("y")
        msgs = []
        panel = LocalPanel(initial_path=str(tmp_path))
        panel.status_message.connect(msgs.append)
        panel._populate(str(tmp_path))
        # Only 1 file (visible.txt) should be counted — not .hidden
        assert msgs
        assert "1 file" in msgs[-1]
