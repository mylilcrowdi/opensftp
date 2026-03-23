"""
Tests for _duplicate_name() — pure naming logic for file duplication.

Covers: n=1 (first copy), n>1 (numbered copies), extensions, dotfiles,
        extension-less names, double extensions, and navigate_or_root()
        fallback behaviour in RemotePanel.
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.ui.panels.remote_panel import _duplicate_name, RemotePanel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def panel(qapp):
    p = RemotePanel()
    yield p
    p.close()
    p.deleteLater()
    QApplication.processEvents()


# ── _duplicate_name() — first copy (n=1) ──────────────────────────────────────

class TestDuplicateNameFirstCopy:
    def test_simple_extension(self):
        assert _duplicate_name("report.pdf") == "report copy.pdf"

    def test_txt_extension(self):
        assert _duplicate_name("readme.txt") == "readme copy.txt"

    def test_no_extension(self):
        assert _duplicate_name("Makefile") == "Makefile copy"

    def test_dotfile_treated_as_no_extension(self):
        """Dotfiles like .bashrc have no displayable extension."""
        assert _duplicate_name(".bashrc") == ".bashrc copy"

    def test_dotfile_with_extension(self):
        """'.env.local' starts with '.', so treated as no extension."""
        assert _duplicate_name(".env.local") == ".env.local copy"

    def test_double_extension_uses_last(self):
        """'archive.tar.gz' → split on last dot → 'archive.tar copy.gz'."""
        assert _duplicate_name("archive.tar.gz") == "archive.tar copy.gz"

    def test_name_with_spaces(self):
        assert _duplicate_name("my document.docx") == "my document copy.docx"

    def test_name_already_has_copy_suffix(self):
        """Duplicating a copy produces 'file copy copy.ext'."""
        assert _duplicate_name("file copy.txt") == "file copy copy.txt"

    def test_image_extension(self):
        assert _duplicate_name("photo.jpg") == "photo copy.jpg"

    def test_hidden_config_file(self):
        assert _duplicate_name(".vimrc") == ".vimrc copy"


# ── _duplicate_name() — numbered copies (n>1) ────────────────────────────────

class TestDuplicateNameNumbered:
    def test_n2_adds_number(self):
        assert _duplicate_name("report.pdf", 2) == "report copy 2.pdf"

    def test_n3_adds_number(self):
        assert _duplicate_name("report.pdf", 3) == "report copy 3.pdf"

    def test_n10_adds_number(self):
        assert _duplicate_name("report.pdf", 10) == "report copy 10.pdf"

    def test_n2_no_extension(self):
        assert _duplicate_name("Makefile", 2) == "Makefile copy 2"

    def test_n2_dotfile(self):
        assert _duplicate_name(".bashrc", 2) == ".bashrc copy 2"

    def test_n1_is_default(self):
        """Calling with explicit n=1 equals the default."""
        assert _duplicate_name("f.txt", 1) == _duplicate_name("f.txt")

    def test_numbered_copy_preserves_extension(self):
        assert _duplicate_name("data.csv", 5) == "data copy 5.csv"


# ── navigate_or_root() ────────────────────────────────────────────────────────

class TestNavigateOrRoot:
    def test_valid_path_calls_navigate_with_that_path(self, panel):
        with patch.object(panel, "navigate") as mock_nav:
            panel.navigate_or_root("/saved/path")
        mock_nav.assert_called_once_with("/saved/path")

    def test_root_path_calls_navigate_root(self, panel):
        with patch.object(panel, "navigate") as mock_nav:
            panel.navigate_or_root("/")
        mock_nav.assert_called_once_with("/")

    def test_empty_string_calls_navigate_root(self, panel):
        with patch.object(panel, "navigate") as mock_nav:
            panel.navigate_or_root("")
        mock_nav.assert_called_once_with("/")

    def test_exception_in_navigate_falls_back_to_root(self, panel):
        """If navigate() raises (e.g. breadcrumb error), falls back to '/'."""
        calls = []

        def _raise_once(path):
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("simulated error")

        with patch.object(panel, "navigate", side_effect=_raise_once):
            panel.navigate_or_root("/bad/path")

        assert calls == ["/bad/path", "/"]

    def test_does_not_navigate_twice_for_valid_path(self, panel):
        """Happy path: navigate() succeeds → navigate('/') is NOT called."""
        with patch.object(panel, "navigate") as mock_nav:
            panel.navigate_or_root("/valid")
        assert mock_nav.call_count == 1
        assert mock_nav.call_args == call("/valid")
