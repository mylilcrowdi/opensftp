"""
Tests for ThemeManager — QSS theme loading and switching.

Covers: initial state, available themes, apply(), toggle(),
        unknown theme raises ValueError, QSS content is non-empty.
"""
from __future__ import annotations

import sys
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.styling.theme_manager import ThemeManager, AVAILABLE_THEMES, DEFAULT_THEME, _system_prefers_dark


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def mgr(qapp):
    return ThemeManager(qapp)


# ── initial state ─────────────────────────────────────────────────────────────

class TestThemeManagerInit:
    def test_current_is_default_theme(self, mgr):
        assert mgr.current == DEFAULT_THEME

    def test_default_theme_is_dark(self, mgr):
        assert mgr.current == "dark"

    def test_available_returns_list(self, mgr):
        result = mgr.available()
        assert isinstance(result, list)

    def test_available_contains_dark_and_light(self, mgr):
        avail = mgr.available()
        assert "dark" in avail
        assert "light" in avail

    def test_available_matches_constant(self, mgr):
        assert set(mgr.available()) == set(AVAILABLE_THEMES)

    def test_available_returns_copy(self, mgr):
        """Mutating the returned list must not affect internal state."""
        a = mgr.available()
        a.clear()
        assert len(mgr.available()) > 0


# ── apply() ───────────────────────────────────────────────────────────────────

class TestThemeManagerApply:
    def test_apply_dark_sets_current_dark(self, mgr):
        mgr.apply("dark")
        assert mgr.current == "dark"

    def test_apply_light_sets_current_light(self, mgr):
        mgr.apply("light")
        assert mgr.current == "light"

    def test_apply_dark_after_light_switches_back(self, mgr):
        mgr.apply("light")
        mgr.apply("dark")
        assert mgr.current == "dark"

    def test_apply_unknown_raises_value_error(self, mgr):
        with pytest.raises(ValueError):
            mgr.apply("neon_pink")

    def test_apply_empty_string_raises_value_error(self, mgr):
        with pytest.raises(ValueError):
            mgr.apply("")

    def test_apply_sets_stylesheet_on_app(self, qapp, mgr):
        mgr.apply("dark")
        assert len(qapp.styleSheet()) > 0

    def test_apply_light_stylesheet_differs_from_dark(self, qapp, mgr):
        mgr.apply("dark")
        dark_css = qapp.styleSheet()
        mgr.apply("light")
        light_css = qapp.styleSheet()
        assert dark_css != light_css


# ── toggle() ─────────────────────────────────────────────────────────────────

class TestThemeManagerToggle:
    def test_toggle_from_dark_returns_light(self, mgr):
        mgr.apply("dark")
        result = mgr.toggle()
        assert result == "light"

    def test_toggle_from_light_returns_dark(self, mgr):
        mgr.apply("light")
        result = mgr.toggle()
        assert result == "dark"

    def test_toggle_updates_current(self, mgr):
        mgr.apply("dark")
        mgr.toggle()
        assert mgr.current == "light"

    def test_double_toggle_returns_to_original(self, mgr):
        mgr.apply("dark")
        mgr.toggle()
        mgr.toggle()
        assert mgr.current == "dark"


# ── _load() — QSS content ─────────────────────────────────────────────────────

class TestThemeQSSContent:
    def test_dark_qss_is_non_empty(self):
        qss = ThemeManager._load("dark")
        assert len(qss.strip()) > 0

    def test_light_qss_is_non_empty(self):
        qss = ThemeManager._load("light")
        assert len(qss.strip()) > 0

    def test_dark_qss_contains_css_like_syntax(self):
        qss = ThemeManager._load("dark")
        # QSS files have property: value pairs
        assert ":" in qss or "{" in qss

    def test_light_qss_contains_css_like_syntax(self):
        qss = ThemeManager._load("light")
        assert ":" in qss or "{" in qss


# ── apply_system_theme() ──────────────────────────────────────────────────────

class TestApplySystemTheme:
    def test_apply_system_theme_returns_valid_theme(self, mgr):
        result = mgr.apply_system_theme()
        assert result in AVAILABLE_THEMES

    def test_apply_system_theme_sets_current(self, mgr):
        result = mgr.apply_system_theme()
        assert mgr.current == result

    def test_system_prefers_dark_returns_bool(self):
        result = _system_prefers_dark()
        assert isinstance(result, bool)

    def test_apply_system_theme_applies_stylesheet(self, qapp, mgr):
        mgr.apply_system_theme()
        assert len(qapp.styleSheet()) > 0
