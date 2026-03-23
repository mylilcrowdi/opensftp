"""
Tests for _LocalDragTree keyboard shortcuts.

Covers: Return/Enter → navigate into directory,
        Backspace → go to parent directory,
        F2 → rename selected item (dialog mocked),
        Delete → delete selected item (dialog mocked),
        key events on non-navigable items are ignored safely.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from sftp_ui.ui.panels.local_panel import LocalPanel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _press(tree, key: Qt.Key) -> None:
    """Send a key press + release event directly to the tree."""
    press = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    tree.keyPressEvent(press)


def _select_item_named(panel: LocalPanel, substring: str) -> bool:
    """Select the first tree item whose text contains `substring`. Returns True if found."""
    panel._tree.clearSelection()
    for i in range(panel._tree.topLevelItemCount()):
        item = panel._tree.topLevelItem(i)
        if substring in item.text(0):
            item.setSelected(True)
            return True
    return False


# ── Return / Enter — navigate into directory ───────────────────────────────────

class TestKeyReturn:
    def test_return_enters_selected_directory(self, qapp, tmp_path):
        sub = tmp_path / "enter_me"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        assert _select_item_named(panel, "enter_me")
        _press(panel._tree, Qt.Key.Key_Return)
        assert panel.current_path() == str(sub)

    def test_enter_key_also_navigates(self, qapp, tmp_path):
        sub = tmp_path / "via_enter"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        assert _select_item_named(panel, "via_enter")
        _press(panel._tree, Qt.Key.Key_Enter)
        assert panel.current_path() == str(sub)

    def test_return_on_file_does_not_navigate(self, qapp, tmp_path):
        (tmp_path / "readme.txt").write_text("hi")
        panel = LocalPanel(initial_path=str(tmp_path))
        original = panel.current_path()
        assert _select_item_named(panel, "readme.txt")
        _press(panel._tree, Qt.Key.Key_Return)
        assert panel.current_path() == original

    def test_return_with_no_selection_does_not_crash(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        panel._tree.clearSelection()
        _press(panel._tree, Qt.Key.Key_Return)  # must not raise
        assert panel.current_path() == str(tmp_path)

    def test_return_with_multiple_selected_does_not_navigate(self, qapp, tmp_path):
        """Return only navigates when exactly one item is selected."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        panel = LocalPanel(initial_path=str(tmp_path))
        for i in range(panel._tree.topLevelItemCount()):
            panel._tree.topLevelItem(i).setSelected(True)
        _press(panel._tree, Qt.Key.Key_Return)
        # With multiple selections → no navigation
        assert panel.current_path() == str(tmp_path)


# ── Backspace — go to parent directory ────────────────────────────────────────

class TestKeyBackspace:
    def test_backspace_goes_to_parent(self, qapp, tmp_path):
        sub = tmp_path / "child"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(sub))
        _press(panel._tree, Qt.Key.Key_Backspace)
        assert panel.current_path() == str(tmp_path)

    def test_backspace_at_root_does_not_crash(self, qapp):
        """At filesystem root, Backspace must not navigate further up."""
        panel = LocalPanel(initial_path="/")
        _press(panel._tree, Qt.Key.Key_Backspace)
        assert panel.current_path() == "/"

    def test_backspace_from_deep_path_goes_one_level_up(self, qapp, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        panel = LocalPanel(initial_path=str(deep))
        _press(panel._tree, Qt.Key.Key_Backspace)
        assert panel.current_path() == str(tmp_path / "a" / "b")

    def test_backspace_emits_path_changed(self, qapp, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        panel = LocalPanel(initial_path=str(sub))
        received = []
        panel.path_changed.connect(received.append)
        _press(panel._tree, Qt.Key.Key_Backspace)
        assert received == [str(tmp_path)]


# ── F2 — rename ────────────────────────────────────────────────────────────────

class TestKeyF2:
    def test_f2_triggers_rename_on_selected_item(self, qapp, tmp_path):
        f = tmp_path / "old.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        assert _select_item_named(panel, "old.txt")
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("new.txt", True)) as mock_get:
            _press(panel._tree, Qt.Key.Key_F2)
        mock_get.assert_called_once()

    def test_f2_renames_file_on_disk(self, qapp, tmp_path):
        f = tmp_path / "before.txt"
        f.write_text("content")
        panel = LocalPanel(initial_path=str(tmp_path))
        assert _select_item_named(panel, "before.txt")
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText",
                   return_value=("after.txt", True)):
            _press(panel._tree, Qt.Key.Key_F2)
        assert (tmp_path / "after.txt").exists()
        assert not (tmp_path / "before.txt").exists()

    def test_f2_with_no_selection_does_not_crash(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        panel._tree.clearSelection()
        _press(panel._tree, Qt.Key.Key_F2)  # must not raise

    def test_f2_with_multiple_selection_does_not_rename(self, qapp, tmp_path):
        """F2 only renames when exactly one item is selected."""
        for name in ("x.txt", "y.txt"):
            (tmp_path / name).write_text(name)
        panel = LocalPanel(initial_path=str(tmp_path))
        for i in range(panel._tree.topLevelItemCount()):
            panel._tree.topLevelItem(i).setSelected(True)
        with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText") as mock_get:
            _press(panel._tree, Qt.Key.Key_F2)
        mock_get.assert_not_called()

    def test_f2_skips_dotdot_item(self, qapp, tmp_path):
        """Pressing F2 on '..' must not rename the parent directory."""
        panel = LocalPanel(initial_path=str(tmp_path))
        # Select the ".." item
        first = panel._tree.topLevelItem(0)
        if first and first.text(0) == "..":
            first.setSelected(True)
            with patch("sftp_ui.ui.panels.local_panel.QInputDialog.getText") as mock_get:
                _press(panel._tree, Qt.Key.Key_F2)
            mock_get.assert_not_called()


# ── Delete — delete selected items ────────────────────────────────────────────

class TestKeyDelete:
    def test_delete_key_removes_selected_file(self, qapp, tmp_path):
        f = tmp_path / "remove_me.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        assert _select_item_named(panel, "remove_me.txt")
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            _press(panel._tree, Qt.Key.Key_Delete)
        assert not f.exists()

    def test_delete_key_cancelled_keeps_file(self, qapp, tmp_path):
        f = tmp_path / "keep_me.txt"
        f.write_text("x")
        panel = LocalPanel(initial_path=str(tmp_path))
        assert _select_item_named(panel, "keep_me.txt")
        with patch("sftp_ui.ui.panels.local_panel.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No):
            _press(panel._tree, Qt.Key.Key_Delete)
        assert f.exists()

    def test_delete_key_with_no_selection_does_not_crash(self, qapp, tmp_path):
        panel = LocalPanel(initial_path=str(tmp_path))
        panel._tree.clearSelection()
        _press(panel._tree, Qt.Key.Key_Delete)  # must not raise
