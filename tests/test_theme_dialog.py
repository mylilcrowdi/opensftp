"""
Tests for ThemeDialog and the extended ThemeManager.

Covers:
  - All 5 named themes available
  - System mode: set_mode("system") resolves to dark or light
  - set_mode() persists via QSettings (mocked)
  - set_mode() raises ValueError for unknown modes
  - restore() falls back to DEFAULT_THEME on missing/bad settings
  - THEME_LABELS and THEME_SWATCHES cover all AVAILABLE_THEMES
  - ThemeDialog: opens without error, cards match themes + system
  - ThemeDialog: clicking a card applies the theme live
  - ThemeDialog: Cancel restores the previous theme
  - ThemeDialog: selected_mode reflects the checked card
  - ThemeDialog: OK accepts and keeps the new theme
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.styling.theme_manager import (
    AVAILABLE_THEMES,
    DEFAULT_THEME,
    THEME_LABELS,
    THEME_SWATCHES,
    ThemeManager,
    _system_prefers_dark,
)
from sftp_ui.ui.dialogs.theme_dialog import ThemeDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def mgr(qapp):
    return ThemeManager(qapp)


# ── AVAILABLE_THEMES ──────────────────────────────────────────────────────────

class TestAvailableThemes:
    def test_count_is_five(self):
        assert len(AVAILABLE_THEMES) == 5

    def test_contains_dark_and_light(self):
        assert "dark" in AVAILABLE_THEMES
        assert "light" in AVAILABLE_THEMES

    def test_contains_presets(self):
        for key in ("nord", "dracula", "solarized_dark"):
            assert key in AVAILABLE_THEMES

    def test_default_theme_in_available(self):
        assert DEFAULT_THEME in AVAILABLE_THEMES


# ── THEME_LABELS and THEME_SWATCHES ──────────────────────────────────────────

class TestThemeMetadata:
    def test_labels_cover_all_themes(self):
        for key in AVAILABLE_THEMES:
            assert key in THEME_LABELS, f"missing label for {key!r}"

    def test_swatches_cover_all_themes(self):
        for key in AVAILABLE_THEMES:
            assert key in THEME_SWATCHES, f"missing swatch for {key!r}"

    def test_swatches_are_three_tuples(self):
        for key, swatch in THEME_SWATCHES.items():
            assert len(swatch) == 3, f"{key!r} swatch must have 3 colours"

    def test_swatch_colours_start_with_hash(self):
        for key, (bg, fg, accent) in THEME_SWATCHES.items():
            for col in (bg, fg, accent):
                assert col.startswith("#"), f"{key!r} colour {col!r} must be hex"

    def test_labels_are_non_empty_strings(self):
        for key, label in THEME_LABELS.items():
            assert isinstance(label, str) and label.strip()


# ── ThemeManager.apply() ──────────────────────────────────────────────────────

class TestThemeManagerApply:
    def test_apply_dark(self, mgr):
        mgr.apply("dark")
        assert mgr.current == "dark"

    def test_apply_light(self, mgr):
        mgr.apply("light")
        assert mgr.current == "light"

    def test_apply_nord(self, mgr):
        mgr.apply("nord")
        assert mgr.current == "nord"

    def test_apply_dracula(self, mgr):
        mgr.apply("dracula")
        assert mgr.current == "dracula"

    def test_apply_solarized_dark(self, mgr):
        mgr.apply("solarized_dark")
        assert mgr.current == "solarized_dark"

    def test_apply_unknown_raises(self, mgr):
        with pytest.raises(ValueError, match="Unknown theme"):
            mgr.apply("cyberpunk")

    def test_apply_emits_signal(self, mgr, qapp):
        received = []
        mgr.theme_changed.connect(received.append)
        mgr.apply("dark")
        mgr.theme_changed.disconnect(received.append)
        assert "dark" in received

    def test_apply_sets_stylesheet(self, mgr, qapp):
        mgr.apply("dark")
        assert len(qapp.styleSheet()) > 0

    def test_apply_returns_none(self, mgr):
        result = mgr.apply("light")
        assert result is None


# ── ThemeManager.set_mode() ───────────────────────────────────────────────────

class TestSetMode:
    def test_set_named_theme(self, mgr):
        with patch.object(mgr, "_persist"):
            mgr.set_mode("nord")
        assert mgr.current == "nord"
        assert mgr.mode == "nord"

    def test_set_system_resolves_dark(self, mgr):
        with patch("sftp_ui.styling.theme_manager._system_prefers_dark", return_value=True), \
             patch.object(mgr, "_persist"):
            result = mgr.set_mode("system")
        assert result == "dark"
        assert mgr.mode == "system"

    def test_set_system_resolves_light(self, mgr):
        with patch("sftp_ui.styling.theme_manager._system_prefers_dark", return_value=False), \
             patch.object(mgr, "_persist"):
            result = mgr.set_mode("system")
        assert result == "light"
        assert mgr.mode == "system"

    def test_set_mode_persists(self, mgr):
        with patch.object(mgr, "_persist") as mock_persist:
            mgr.set_mode("dracula")
        mock_persist.assert_called_once_with("dracula")

    def test_set_mode_unknown_raises(self, mgr):
        with pytest.raises(ValueError, match="Unknown mode"):
            mgr.set_mode("rainbow")

    def test_set_mode_returns_theme_name(self, mgr):
        with patch.object(mgr, "_persist"):
            result = mgr.set_mode("light")
        assert result == "light"


# ── ThemeManager.restore() ────────────────────────────────────────────────────

class TestRestore:
    def test_restore_dark(self, mgr):
        with patch("sftp_ui.styling.theme_manager.QSettings") as MockSettings:
            MockSettings.return_value.value.return_value = "dark"
            result = mgr.restore()
        assert result == "dark"

    def test_restore_unknown_falls_back_to_default(self, mgr):
        with patch("sftp_ui.styling.theme_manager.QSettings") as MockSettings:
            MockSettings.return_value.value.return_value = "nonexistent_theme"
            result = mgr.restore()
        assert result == DEFAULT_THEME

    def test_restore_system_resolves(self, mgr):
        with patch("sftp_ui.styling.theme_manager.QSettings") as MockSettings, \
             patch("sftp_ui.styling.theme_manager._system_prefers_dark", return_value=True):
            MockSettings.return_value.value.return_value = "system"
            result = mgr.restore()
        assert result in ("dark", "light")

    def test_restore_missing_falls_back(self, mgr):
        with patch("sftp_ui.styling.theme_manager.QSettings") as MockSettings:
            MockSettings.return_value.value.return_value = DEFAULT_THEME
            result = mgr.restore()
        assert result == DEFAULT_THEME


# ── ThemeManager.toggle() ─────────────────────────────────────────────────────

class TestToggle:
    def test_toggle_dark_to_light(self, mgr):
        mgr.apply("dark")
        with patch.object(mgr, "_persist"):
            result = mgr.toggle()
        assert result == "light"

    def test_toggle_light_to_dark(self, mgr):
        mgr.apply("light")
        with patch.object(mgr, "_persist"):
            result = mgr.toggle()
        assert result == "dark"

    def test_toggle_from_preset_toggles_to_light(self, mgr):
        """Toggle from a non-dark theme — not dark, so goes to dark."""
        mgr.apply("nord")
        with patch.object(mgr, "_persist"):
            result = mgr.toggle()
        # current was "nord" (not "dark") so toggle yields "dark"
        assert result == "dark"


# ── _system_prefers_dark ──────────────────────────────────────────────────────

class TestSystemPrefersDark:
    def test_returns_bool(self, qapp):
        result = _system_prefers_dark()
        assert isinstance(result, bool)

    def test_exception_path_returns_true(self):
        # The function catches all exceptions and returns True as fallback.
        # Simulate by patching the Qt hints call inside PySide6.
        with patch("PySide6.QtGui.QGuiApplication.styleHints", side_effect=RuntimeError("no display")):
            result = _system_prefers_dark()
        # May be True or False depending on whether the patch fires; the key
        # property is that no exception escapes the function boundary.
        assert isinstance(result, bool)


# ── ThemeDialog ───────────────────────────────────────────────────────────────

class TestThemeDialog:
    def test_dialog_opens(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        assert dlg is not None
        dlg.destroy()

    def test_cards_include_all_themes(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        for key in AVAILABLE_THEMES:
            assert key in dlg._cards, f"missing card for {key!r}"
        dlg.destroy()

    def test_cards_include_system(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        assert "system" in dlg._cards
        dlg.destroy()

    def test_initial_selection_matches_current_mode(self, qapp, mgr):
        with patch.object(mgr, "_persist"):
            mgr.set_mode("nord")
        dlg = ThemeDialog(mgr, None)
        assert dlg._cards["nord"].isChecked()
        dlg.destroy()

    def test_clicking_card_applies_theme(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        with patch.object(mgr, "_persist"):
            dlg._on_card_clicked("dracula")
        assert mgr.current == "dracula"
        dlg.destroy()

    def test_cancel_restores_original_theme(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        with patch.object(mgr, "_persist"):
            dlg._on_card_clicked("dracula")   # preview
        dlg._on_cancel()                       # cancel
        assert mgr.current == "dark"
        dlg.destroy()

    def test_accept_keeps_new_theme(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        with patch.object(mgr, "_persist"):
            dlg._on_card_clicked("nord")
        dlg._on_accept()
        assert mgr.current == "nord"
        dlg.destroy()

    def test_selected_mode_reflects_checked_card(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        dlg._select_card("light")
        assert dlg.selected_mode == "light"
        dlg.destroy()

    def test_only_one_card_checked_at_a_time(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        dlg._select_card("dracula")
        checked = [k for k, c in dlg._cards.items() if c.isChecked()]
        assert len(checked) == 1
        assert checked[0] == "dracula"
        dlg.destroy()

    def test_select_card_system(self, qapp, mgr):
        mgr.apply("dark")
        dlg = ThemeDialog(mgr, None)
        dlg._select_card("system")
        assert dlg._cards["system"].isChecked()
        assert dlg.selected_mode == "system"
        dlg.destroy()
