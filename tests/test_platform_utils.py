"""
Tests for sftp_ui.core.platform_utils — cross-platform helpers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sftp_ui.core.platform_utils import (
    config_dir,
    file_manager_action_label,
    open_in_file_manager,
    PLATFORM,
)


# ── config_dir ───────────────────────────────────────────────────────────────

class TestConfigDir:
    def test_returns_path_object(self):
        assert isinstance(config_dir(), Path)

    def test_ends_with_sftp_ui(self):
        assert config_dir().name == "sftp-ui"

    def test_macos_uses_dot_config(self, monkeypatch):
        monkeypatch.setattr("sftp_ui.core.platform_utils.PLATFORM", "darwin")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        # Re-import the function to pick up monkeypatched PLATFORM
        import importlib
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "darwin")
        result = pu.config_dir()
        assert ".config" in str(result) or "sftp-ui" in str(result)

    def test_linux_uses_xdg_config_home_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        result = pu.config_dir()
        assert result == tmp_path / "sftp-ui"

    def test_linux_falls_back_to_dot_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        result = pu.config_dir()
        assert result == Path.home() / ".config" / "sftp-ui"

    def test_windows_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "win32")
        result = pu.config_dir()
        assert result == tmp_path / "sftp-ui"

    def test_windows_falls_back_when_appdata_missing(self, monkeypatch):
        monkeypatch.delenv("APPDATA", raising=False)
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "win32")
        result = pu.config_dir()
        assert result.name == "sftp-ui"
        assert "AppData" in str(result) or "sftp-ui" in str(result)


# ── file_manager_action_label ────────────────────────────────────────────────

class TestFileManagerLabel:
    def test_darwin_returns_finder(self, monkeypatch):
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "darwin")
        assert "Finder" in pu.file_manager_action_label()

    def test_win32_returns_explorer(self, monkeypatch):
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "win32")
        assert "Explorer" in pu.file_manager_action_label()

    def test_linux_returns_file_manager(self, monkeypatch):
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        label = pu.file_manager_action_label()
        assert "File Manager" in label

    def test_label_contains_arrow(self, monkeypatch):
        import sftp_ui.core.platform_utils as pu
        for platform in ("darwin", "win32", "linux"):
            monkeypatch.setattr(pu, "PLATFORM", platform)
            assert "↗" in pu.file_manager_action_label()

    def test_is_dir_flag_accepted(self, monkeypatch):
        """is_dir kwarg should not raise regardless of value."""
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        pu.file_manager_action_label(is_dir=True)
        pu.file_manager_action_label(is_dir=False)


# ── open_in_file_manager ─────────────────────────────────────────────────────

class TestOpenInFileManager:
    """
    We never want to actually open a file manager during tests — so we
    monkeypatch subprocess.Popen and verify it is called with the right args.
    """

    def test_macos_calls_open_R(self, monkeypatch, tmp_path):
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "darwin")
        mock_popen = MagicMock()
        monkeypatch.setattr(pu.subprocess, "Popen", mock_popen)
        pu.open_in_file_manager(str(tmp_path))
        mock_popen.assert_called_once_with(["open", "-R", str(tmp_path)])

    def test_windows_calls_explorer_select(self, monkeypatch, tmp_path):
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "win32")
        mock_popen = MagicMock()
        monkeypatch.setattr(pu.subprocess, "Popen", mock_popen)
        pu.open_in_file_manager(str(tmp_path))
        args = mock_popen.call_args[0][0]
        assert args[0] == "explorer"
        assert str(tmp_path) in args[1]

    def test_linux_calls_xdg_open(self, monkeypatch, tmp_path):
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        mock_popen = MagicMock()
        monkeypatch.setattr(pu.subprocess, "Popen", mock_popen)
        pu.open_in_file_manager(str(tmp_path))
        args = mock_popen.call_args[0][0]
        assert args[0] == "xdg-open"

    def test_linux_file_opens_parent(self, monkeypatch, tmp_path):
        """When the path is a file, Linux should open the parent directory."""
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        mock_popen = MagicMock()
        monkeypatch.setattr(pu.subprocess, "Popen", mock_popen)
        f = tmp_path / "test.txt"
        f.write_text("x")
        pu.open_in_file_manager(str(f))
        args = mock_popen.call_args[0][0]
        # Should open the parent dir, not the file itself
        assert args[1] == str(tmp_path)

    def test_linux_fallback_to_nautilus(self, monkeypatch, tmp_path):
        """When xdg-open is missing, try common file managers."""
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")

        call_count = {"n": 0}

        def fake_popen(args, **kwargs):
            call_count["n"] += 1
            if args[0] == "xdg-open":
                raise FileNotFoundError
            return MagicMock()

        monkeypatch.setattr(pu.subprocess, "Popen", fake_popen)
        # Should not raise even if xdg-open is missing
        pu.open_in_file_manager(str(tmp_path))
        # At least 2 Popen calls: one failing, one succeeding
        assert call_count["n"] >= 2

    def test_linux_no_file_manager_available(self, monkeypatch, tmp_path):
        """If no file manager at all is found, should silently give up (no raise)."""
        import sftp_ui.core.platform_utils as pu
        monkeypatch.setattr(pu, "PLATFORM", "linux")
        monkeypatch.setattr(pu.subprocess, "Popen", MagicMock(side_effect=FileNotFoundError))
        # Must not raise
        pu.open_in_file_manager(str(tmp_path))


# ── PLATFORM constant ────────────────────────────────────────────────────────

class TestPlatformConstant:
    def test_is_string(self):
        assert isinstance(PLATFORM, str)

    def test_is_known_value(self):
        assert PLATFORM in ("darwin", "win32", "linux")


# ── Integration: connection.py and ui_state.py use platform_utils ────────────

class TestConfigDirIntegration:
    def test_connection_store_default_path_uses_config_dir(self, monkeypatch):
        """ConnectionStore's default path should match config_dir()."""
        import sftp_ui.core.connection as conn_mod
        import sftp_ui.core.platform_utils as pu
        expected = pu.config_dir() / "connections.json"
        assert conn_mod.DEFAULT_CONFIG_PATH == expected

    def test_ui_state_default_path_uses_config_dir(self, monkeypatch):
        """UIState's default path should match config_dir()."""
        import sftp_ui.core.ui_state as state_mod
        import sftp_ui.core.platform_utils as pu
        expected = pu.config_dir() / "ui_state.json"
        assert state_mod.DEFAULT_STATE_PATH == expected
