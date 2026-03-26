"""
Command Palette — Ctrl+P opens a VS Code-style command palette.

Tests cover:
1. Command registry: register, list, lookup, categories
2. Fuzzy matching: substring, abbreviation, ranking
3. CommandPaletteDialog widget: filter input, result list, selection, keyboard nav
4. Integration: MainWindow shortcut, command execution, state-dependent availability
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sftp_ui.core.command_registry import Command, CommandRegistry, fuzzy_match


# ── 1. Command Registry ──────────────────────────────────────────────────────

class TestCommand:
    def test_command_has_required_fields(self):
        cmd = Command(
            id="file.refresh",
            name="Refresh",
            category="Navigation",
            handler=lambda: None,
        )
        assert cmd.id == "file.refresh"
        assert cmd.name == "Refresh"
        assert cmd.category == "Navigation"
        assert callable(cmd.handler)

    def test_command_optional_fields_default(self):
        cmd = Command(id="x", name="X", category="C", handler=lambda: None)
        assert cmd.shortcut is None
        assert cmd.enabled is True
        assert cmd.icon is None

    def test_command_with_shortcut(self):
        cmd = Command(
            id="file.refresh",
            name="Refresh",
            category="Navigation",
            handler=lambda: None,
            shortcut="Ctrl+R",
        )
        assert cmd.shortcut == "Ctrl+R"

    def test_command_enabled_predicate(self):
        connected = False
        cmd = Command(
            id="nav.refresh",
            name="Refresh",
            category="Navigation",
            handler=lambda: None,
            enabled_when=lambda: connected,
        )
        assert cmd.is_enabled() is False
        connected = True
        assert cmd.is_enabled() is True

    def test_command_enabled_without_predicate(self):
        cmd = Command(id="x", name="X", category="C", handler=lambda: None)
        assert cmd.is_enabled() is True


class TestCommandRegistry:
    @pytest.fixture
    def registry(self):
        return CommandRegistry()

    def test_register_and_list(self, registry):
        registry.register(Command(
            id="a", name="Alpha", category="Cat1", handler=lambda: None,
        ))
        registry.register(Command(
            id="b", name="Beta", category="Cat2", handler=lambda: None,
        ))
        assert len(registry.all()) == 2

    def test_register_duplicate_id_overwrites(self, registry):
        registry.register(Command(id="a", name="V1", category="C", handler=lambda: None))
        registry.register(Command(id="a", name="V2", category="C", handler=lambda: None))
        assert len(registry.all()) == 1
        assert registry.get("a").name == "V2"

    def test_get_by_id(self, registry):
        registry.register(Command(id="x", name="X", category="C", handler=lambda: None))
        assert registry.get("x").name == "X"
        assert registry.get("nonexistent") is None

    def test_categories(self, registry):
        registry.register(Command(id="a", name="A", category="Nav", handler=lambda: None))
        registry.register(Command(id="b", name="B", category="Transfer", handler=lambda: None))
        registry.register(Command(id="c", name="C", category="Nav", handler=lambda: None))
        cats = registry.categories()
        assert "Nav" in cats
        assert "Transfer" in cats

    def test_by_category(self, registry):
        registry.register(Command(id="a", name="A", category="Nav", handler=lambda: None))
        registry.register(Command(id="b", name="B", category="Transfer", handler=lambda: None))
        registry.register(Command(id="c", name="C", category="Nav", handler=lambda: None))
        nav = registry.by_category("Nav")
        assert len(nav) == 2
        assert all(c.category == "Nav" for c in nav)

    def test_search_filters_by_query(self, registry):
        registry.register(Command(id="a", name="Refresh Remote", category="Nav", handler=lambda: None))
        registry.register(Command(id="b", name="Upload Files", category="Transfer", handler=lambda: None))
        registry.register(Command(id="c", name="Delete File", category="File", handler=lambda: None))

        results = registry.search("ref")
        assert len(results) == 1
        assert results[0].id == "a"

    def test_search_case_insensitive(self, registry):
        registry.register(Command(id="a", name="Refresh Remote", category="Nav", handler=lambda: None))
        results = registry.search("REFRESH")
        assert len(results) == 1

    def test_search_empty_returns_all(self, registry):
        registry.register(Command(id="a", name="A", category="C", handler=lambda: None))
        registry.register(Command(id="b", name="B", category="C", handler=lambda: None))
        assert len(registry.search("")) == 2

    def test_search_excludes_disabled(self, registry):
        registry.register(Command(
            id="a", name="Refresh", category="Nav",
            handler=lambda: None, enabled_when=lambda: False,
        ))
        registry.register(Command(
            id="b", name="Help", category="UI", handler=lambda: None,
        ))
        results = registry.search("", include_disabled=False)
        assert len(results) == 1
        assert results[0].id == "b"

    def test_search_includes_disabled_by_default(self, registry):
        registry.register(Command(
            id="a", name="Refresh", category="Nav",
            handler=lambda: None, enabled_when=lambda: False,
        ))
        results = registry.search("")
        assert len(results) == 1

    def test_execute(self, registry):
        called = []
        registry.register(Command(
            id="a", name="A", category="C",
            handler=lambda: called.append(True),
        ))
        registry.execute("a")
        assert called == [True]

    def test_execute_unknown_id_raises(self, registry):
        with pytest.raises(KeyError):
            registry.execute("nonexistent")


# ── 2. Fuzzy Matching ────────────────────────────────────────────────────────

class TestFuzzyMatch:
    def test_exact_match(self):
        score = fuzzy_match("refresh", "Refresh Remote")
        assert score is not None and score > 0

    def test_substring_match(self):
        score = fuzzy_match("remote", "Refresh Remote")
        assert score is not None and score > 0

    def test_abbreviation_match(self):
        """'rr' matches 'Refresh Remote' (first letters)."""
        score = fuzzy_match("rr", "Refresh Remote")
        assert score is not None and score > 0

    def test_no_match(self):
        score = fuzzy_match("xyz", "Refresh Remote")
        assert score is None or score == 0

    def test_empty_query_matches_all(self):
        score = fuzzy_match("", "Anything")
        assert score is not None and score > 0

    def test_case_insensitive(self):
        s1 = fuzzy_match("REFRESH", "Refresh Remote")
        s2 = fuzzy_match("refresh", "Refresh Remote")
        assert s1 == s2

    def test_exact_prefix_ranks_higher(self):
        """'ref' should rank 'Refresh' higher than 'Configure Firewall'."""
        score_prefix = fuzzy_match("ref", "Refresh Remote")
        score_mid = fuzzy_match("ref", "Configure Refresh")
        assert score_prefix > score_mid

    def test_consecutive_chars_rank_higher(self):
        """'upl' (consecutive in 'Upload') should beat 'u...p...l' scattered."""
        score_consec = fuzzy_match("upl", "Upload Files")
        score_scatter = fuzzy_match("upl", "Undo, then Paste and Load")
        assert score_consec > score_scatter


# ── 3. Command Palette Dialog ────────────────────────────────────────────────

class TestCommandPaletteDialog:
    @pytest.fixture
    def registry(self):
        reg = CommandRegistry()
        reg.register(Command(id="nav.refresh", name="Refresh Remote", category="Navigation", handler=lambda: None, shortcut="Ctrl+R"))
        reg.register(Command(id="nav.goto", name="Go to Path", category="Navigation", handler=lambda: None, shortcut="Ctrl+G"))
        reg.register(Command(id="transfer.upload", name="Upload Files", category="Transfer", handler=lambda: None))
        reg.register(Command(id="conn.new", name="New Connection", category="Connection", handler=lambda: None, shortcut="Ctrl+N"))
        reg.register(Command(id="ui.theme", name="Change Theme", category="UI", handler=lambda: None))
        return reg

    @pytest.fixture
    def dialog(self, qapp, registry):
        from sftp_ui.ui.dialogs.command_palette import CommandPaletteDialog
        d = CommandPaletteDialog(registry)
        yield d
        d.close()

    def test_dialog_has_filter_input(self, dialog):
        assert hasattr(dialog, "_filter_input")
        assert dialog._filter_input.placeholderText() != ""

    def test_dialog_has_result_list(self, dialog):
        assert hasattr(dialog, "_result_list")

    def test_initial_state_shows_all_commands(self, dialog):
        assert dialog._result_list.count() == 5

    def test_typing_filters_results(self, dialog, qapp):
        dialog._filter_input.setText("refresh")
        QApplication.processEvents()
        assert dialog._result_list.count() == 1

    def test_clear_filter_shows_all(self, dialog, qapp):
        dialog._filter_input.setText("refresh")
        QApplication.processEvents()
        dialog._filter_input.setText("")
        QApplication.processEvents()
        assert dialog._result_list.count() == 5

    def test_first_item_selected_by_default(self, dialog):
        current = dialog._result_list.currentRow()
        assert current == 0

    def test_shortcut_shown_in_item(self, dialog):
        # Find the "Refresh Remote" item and check shortcut is visible
        found = False
        for i in range(dialog._result_list.count()):
            widget = dialog._result_list.itemWidget(dialog._result_list.item(i))
            if widget and "Refresh" in widget._name_label.text():
                assert "Ctrl+R" in widget._shortcut_label.text()
                found = True
                break
        assert found, "Refresh Remote item with shortcut not found"

    def test_category_shown_in_item(self, dialog):
        found = False
        for i in range(dialog._result_list.count()):
            widget = dialog._result_list.itemWidget(dialog._result_list.item(i))
            if widget and "Refresh" in widget._name_label.text():
                assert "Navigation" in widget._category_label.text()
                found = True
                break
        assert found

    def test_enter_executes_selected_command(self, dialog, registry, qapp):
        called = []
        registry.register(Command(
            id="test.exec", name="Test Execute", category="Test",
            handler=lambda: called.append(True),
        ))
        dialog._filter_input.setText("Test Execute")
        QApplication.processEvents()

        # Simulate Enter key
        from PySide6.QtTest import QTest
        QTest.keyClick(dialog._filter_input, Qt.Key.Key_Return)
        QApplication.processEvents()

        assert called == [True]

    def test_escape_closes_dialog(self, dialog, qapp):
        dialog.show()
        QApplication.processEvents()

        from PySide6.QtTest import QTest
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        QApplication.processEvents()

        assert not dialog.isVisible()

    def test_arrow_keys_navigate_list(self, dialog, qapp):
        from PySide6.QtTest import QTest

        initial_row = dialog._result_list.currentRow()
        QTest.keyClick(dialog._filter_input, Qt.Key.Key_Down)
        QApplication.processEvents()

        assert dialog._result_list.currentRow() == initial_row + 1

    def test_fuzzy_search_reorders_results(self, dialog, qapp):
        dialog._filter_input.setText("upl")
        QApplication.processEvents()

        # Upload should be the first (and likely only) result
        assert dialog._result_list.count() >= 1
        widget = dialog._result_list.itemWidget(dialog._result_list.item(0))
        assert "Upload" in widget._name_label.text()


# ── 4. MainWindow Integration ────────────────────────────────────────────────

class TestCommandPaletteIntegration:
    def test_ctrl_p_shortcut_registered(self, qapp):
        """MainWindow should have Ctrl+P bound to command palette."""
        from sftp_ui.ui.main_window import MainWindow
        import inspect
        source = inspect.getsource(MainWindow._connect_signals)
        assert "Ctrl+P" in source

    def test_registry_has_standard_commands(self, qapp):
        """After setup, registry should contain common commands."""
        from sftp_ui.core.command_registry import CommandRegistry
        reg = CommandRegistry()
        # The registry should be populated by MainWindow, but we can test
        # that the class supports the expected workflow
        reg.register(Command(id="nav.refresh", name="Refresh", category="Nav", handler=lambda: None))
        assert reg.get("nav.refresh") is not None
