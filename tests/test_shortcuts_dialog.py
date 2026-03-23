"""
Tests for ShortcutsDialog — keyboard shortcut help overlay.

Covers:
- Dialog opens and renders all expected sections
- Total shortcut count matches the data definition
- Filter/search hides non-matching rows
- Filter hides section headers when all their rows are hidden
- Clearing filter restores all rows
- Visible row count accessors work correctly
- MainWindow _show_shortcuts_dialog creates a ShortcutsDialog
- F1 and Ctrl+? shortcuts are registered in MainWindow
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sftp_ui.ui.dialogs.shortcuts_dialog import (
    ShortcutsDialog,
    _SHORTCUT_GROUPS,
    _ShortcutRow,
    _SectionHeader,
)


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def dlg(qapp):
    d = ShortcutsDialog()
    d.show()  # must be shown so isVisible() reflects explicit hide/show state
    yield d
    d.close()


# ── basic structure ────────────────────────────────────────────────────────────

class TestShortcutsDialogStructure:
    def test_window_title(self, dlg: ShortcutsDialog):
        assert dlg.windowTitle() == "Keyboard Shortcuts"

    def test_minimum_size(self, dlg: ShortcutsDialog):
        assert dlg.minimumWidth() >= 400
        assert dlg.minimumHeight() >= 300

    def test_section_count_matches_data(self, dlg: ShortcutsDialog):
        assert dlg.section_count() == len(_SHORTCUT_GROUPS)

    def test_total_shortcut_count_matches_data(self, dlg: ShortcutsDialog):
        expected = sum(len(shortcuts) for _, shortcuts in _SHORTCUT_GROUPS)
        assert dlg.total_shortcut_count() == expected

    def test_all_rows_visible_initially(self, dlg: ShortcutsDialog):
        expected = sum(len(shortcuts) for _, shortcuts in _SHORTCUT_GROUPS)
        assert dlg.visible_row_count() == expected

    def test_has_filter_edit(self, dlg: ShortcutsDialog):
        assert dlg.filter_edit is not None
        assert dlg.filter_edit.placeholderText() != ""

    def test_filter_has_clear_button(self, dlg: ShortcutsDialog):
        # isClearButtonEnabled is the PySide6 accessor
        assert dlg.filter_edit.isClearButtonEnabled()


# ── shortcut data integrity ────────────────────────────────────────────────────

class TestShortcutData:
    def test_at_least_four_sections(self):
        assert len(_SHORTCUT_GROUPS) >= 4

    def test_each_group_has_title_and_shortcuts(self):
        for title, shortcuts in _SHORTCUT_GROUPS:
            assert isinstance(title, str) and title
            assert isinstance(shortcuts, list) and len(shortcuts) > 0

    def test_each_shortcut_has_keys_and_description(self):
        for _, shortcuts in _SHORTCUT_GROUPS:
            for keys, desc in shortcuts:
                assert isinstance(keys, str) and keys
                assert isinstance(desc, str) and desc

    def test_f1_shortcut_present(self):
        all_keys = [k for _, shortcuts in _SHORTCUT_GROUPS for k, _ in shortcuts]
        assert any("F1" in k for k in all_keys)

    def test_ctrl_k_present(self):
        all_keys = [k for _, shortcuts in _SHORTCUT_GROUPS for k, _ in shortcuts]
        assert any("Ctrl+K" in k for k in all_keys)

    def test_ctrl_n_present(self):
        all_keys = [k for _, shortcuts in _SHORTCUT_GROUPS for k, _ in shortcuts]
        assert any("Ctrl+N" in k for k in all_keys)


# ── filter / search ────────────────────────────────────────────────────────────

class TestShortcutsDialogFilter:
    def test_filter_by_key_hides_non_matching(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("F5")
        assert dlg.visible_row_count() < dlg.total_shortcut_count()

    def test_filter_by_key_shows_at_least_one(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("F5")
        assert dlg.visible_row_count() >= 1

    def test_filter_by_description_word(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("refresh")
        assert dlg.visible_row_count() >= 1

    def test_filter_is_case_insensitive(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("CONNECT")
        count_upper = dlg.visible_row_count()
        dlg.filter_edit.setText("connect")
        count_lower = dlg.visible_row_count()
        assert count_upper == count_lower

    def test_filter_nonsense_hides_all(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("xyzzy_no_match_ever_123")
        assert dlg.visible_row_count() == 0

    def test_clear_filter_restores_all(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("connect")
        dlg.filter_edit.clear()
        assert dlg.visible_row_count() == dlg.total_shortcut_count()

    def test_empty_filter_shows_all(self, dlg: ShortcutsDialog):
        dlg.filter_edit.setText("")
        assert dlg.visible_row_count() == dlg.total_shortcut_count()

    def test_filter_hides_section_headers_when_no_match(self, dlg: ShortcutsDialog, qapp):
        """When all rows in a section are hidden, its header should also be hidden."""
        # Use a query that only matches rows in the first section
        first_section_title, first_section_shortcuts = _SHORTCUT_GROUPS[0]
        # Find a key unique to the first group
        first_key = first_section_shortcuts[0][0].lower().split("/")[0].strip()
        if not first_key:
            pytest.skip("Could not determine a unique first-section key")

        dlg.filter_edit.setText(first_key)
        # The dialog's _rows list maps sections to their row widgets
        # At least one OTHER section header should be hidden
        headers_visible = [
            header.isVisible()
            for header, _ in dlg._rows
        ]
        # At least the sections that don't contain first_key should be hidden
        assert not all(headers_visible), "Expected some section headers to be hidden by filter"


# ── _ShortcutRow unit tests ────────────────────────────────────────────────────

class TestShortcutRow:
    def test_matches_returns_true_for_empty_query(self, qapp):
        row = _ShortcutRow("Ctrl+K", "Connect / Disconnect")
        assert row.matches("") is True

    def test_matches_key_substring(self, qapp):
        row = _ShortcutRow("Ctrl+K", "Connect / Disconnect")
        assert row.matches("ctrl") is True

    def test_matches_description_substring(self, qapp):
        row = _ShortcutRow("Ctrl+K", "Connect / Disconnect")
        assert row.matches("disconnect") is True

    def test_does_not_match_unrelated_query(self, qapp):
        row = _ShortcutRow("Ctrl+K", "Connect / Disconnect")
        assert row.matches("refresh") is False

    def test_case_insensitive_match(self, qapp):
        row = _ShortcutRow("Ctrl+K", "Connect / Disconnect")
        assert row.matches("CTRL") is True
        assert row.matches("CONNECT") is True


# ── MainWindow integration ─────────────────────────────────────────────────────

class TestMainWindowIntegration:
    @pytest.fixture
    def main_win(self, qapp, tmp_path):
        """Create a MainWindow with a temp-based ConnectionStore."""
        from sftp_ui.core.connection import ConnectionStore
        from sftp_ui.ui.main_window import MainWindow

        store = ConnectionStore(path=tmp_path / "connections.json")
        win = MainWindow(store=store)
        yield win
        win.close()

    def test_shortcuts_dialog_opens(self, main_win, monkeypatch):
        """_show_shortcuts_dialog should instantiate and exec a ShortcutsDialog."""
        opened = []

        def _fake_exec(self_dlg):
            opened.append(self_dlg)
            return 0

        monkeypatch.setattr(ShortcutsDialog, "exec", _fake_exec)
        main_win._show_shortcuts_dialog()
        assert len(opened) == 1
        assert isinstance(opened[0], ShortcutsDialog)

    def test_f1_shortcut_registered(self, main_win):
        """F1 shortcut should be registered in MainWindow."""
        from PySide6.QtGui import QKeySequence
        shortcuts = [
            sc.key().toString()
            for sc in main_win.findChildren(
                __import__("PySide6.QtGui", fromlist=["QShortcut"]).QShortcut
            )
        ]
        assert "F1" in shortcuts

    def test_ctrl_question_shortcut_registered(self, main_win):
        """Ctrl+? shortcut should be registered in MainWindow."""
        from PySide6.QtGui import QKeySequence, QShortcut
        shortcuts = [
            sc.key().toString()
            for sc in main_win.findChildren(QShortcut)
        ]
        # Ctrl+? may render as "Ctrl+?" or "Ctrl+Shift+/" depending on platform
        assert any("Ctrl" in s and ("?" in s or "/" in s) for s in shortcuts)
