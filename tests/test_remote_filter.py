"""
Tests for Remote Panel file filter — Feature 3.

Covers:
  - Filter bar widget exists
  - Typing in filter shows only matching entries
  - Filter is case-insensitive
  - ".." parent entry is never hidden by the filter
  - Clearing the filter restores all entries
  - Filter combines with hidden-files toggle correctly
"""
from __future__ import annotations

import sys
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sftp_ui.core.sftp_client import RemoteEntry


def _make_entry(name: str, is_dir: bool = False) -> RemoteEntry:
    return RemoteEntry(name=name, path=f"/{name}", is_dir=is_dir, size=0, mtime=0)


def _make_panel():
    from PySide6.QtWidgets import QApplication
    import sys
    QApplication.instance() or QApplication(sys.argv)
    from sftp_ui.ui.panels.remote_panel import RemotePanel
    panel = RemotePanel()
    return panel


def _visible_names(panel) -> list[str]:
    model = panel._model
    return [model.entry(r).name for r in range(model.rowCount())]


class TestRemotePanelFilter:
    @pytest.fixture(autouse=True)
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication(sys.argv)

    @pytest.fixture
    def panel_with_entries(self):
        panel = _make_panel()
        entries = [
            _make_entry("..", is_dir=True),
            _make_entry("alpha.txt"),
            _make_entry("beta.csv"),
            _make_entry("gamma.py"),
            _make_entry("AlphaDir", is_dir=True),
        ]
        panel._all_entries = entries
        panel._apply_entries()
        return panel

    def test_filter_edit_exists(self):
        panel = _make_panel()
        assert hasattr(panel, "_filter_edit")

    def test_empty_filter_shows_all(self, panel_with_entries):
        names = _visible_names(panel_with_entries)
        assert "alpha.txt" in names
        assert "beta.csv" in names
        assert "gamma.py" in names

    def test_filter_hides_non_matching(self, panel_with_entries):
        panel_with_entries._filter_edit.setText("alpha")
        names = _visible_names(panel_with_entries)
        assert "alpha.txt" in names
        assert "AlphaDir" in names   # case-insensitive
        assert "beta.csv" not in names
        assert "gamma.py" not in names

    def test_filter_is_case_insensitive(self, panel_with_entries):
        panel_with_entries._filter_edit.setText("ALPHA")
        names = _visible_names(panel_with_entries)
        assert "alpha.txt" in names
        assert "AlphaDir" in names

    def test_dotdot_always_visible(self, panel_with_entries):
        panel_with_entries._filter_edit.setText("zzz_no_match")
        names = _visible_names(panel_with_entries)
        assert ".." in names

    def test_clear_filter_restores_all(self, panel_with_entries):
        panel_with_entries._filter_edit.setText("beta")
        panel_with_entries._filter_edit.clear()
        names = _visible_names(panel_with_entries)
        assert "alpha.txt" in names
        assert "gamma.py" in names

    def test_filter_with_hidden_files_off(self):
        panel = _make_panel()
        entries = [
            _make_entry("..", is_dir=True),
            _make_entry(".dotfile"),
            _make_entry("visible.txt"),
        ]
        panel._all_entries = entries
        panel._show_hidden = False
        panel._filter_edit.setText("")
        panel._apply_entries()
        names = _visible_names(panel)
        assert ".dotfile" not in names
        assert "visible.txt" in names

    def test_filter_extension_match(self, panel_with_entries):
        panel_with_entries._filter_edit.setText(".csv")
        names = _visible_names(panel_with_entries)
        assert "beta.csv" in names
        assert "alpha.txt" not in names
