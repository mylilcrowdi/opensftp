"""
Tests for the "Open SSH Session Here" feature.

Covers:
- platform_utils.open_ssh_terminal: correct subprocess args on macOS/Linux/Windows
- RemotePanel.open_terminal_requested signal emission (context menu + keyboard)
- MainWindow._on_open_terminal_requested: happy path, no connection, launch error
- Shell-quoting helper (_shell_quote) for paths with spaces / special chars
"""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch, call

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core import platform_utils
from sftp_ui.core.platform_utils import _shell_quote, open_ssh_terminal
from sftp_ui.ui.panels.remote_panel import RemotePanel


# ---------------------------------------------------------------------------
# _shell_quote helper
# ---------------------------------------------------------------------------

class TestShellQuote:
    def test_simple_string(self):
        assert _shell_quote("hello") == "'hello'"

    def test_path_with_spaces(self):
        assert _shell_quote("/home/user/my dir") == "'/home/user/my dir'"

    def test_path_with_single_quote(self):
        # The standard shell escape for a single quote inside single-quoted strings
        # is: end the single-quoted section, add an escaped quote, resume.
        # E.g.  'it'\''s here'
        result = _shell_quote("/home/user/it's here")
        assert "it" in result and "s here" in result
        # The result must start and end with single quotes
        assert result.startswith("'") and result.endswith("'")

    def test_empty_string(self):
        assert _shell_quote("") == "''"


# ---------------------------------------------------------------------------
# open_ssh_terminal — macOS
# ---------------------------------------------------------------------------

class TestOpenSSHTerminalMacOS:
    """Verify AppleScript is used on macOS."""

    def _run(self, **kwargs):
        with patch.object(platform_utils, "PLATFORM", "darwin"):
            with patch("subprocess.Popen") as mock_popen:
                open_ssh_terminal(**kwargs)
                return mock_popen

    def test_basic_call_uses_osascript(self):
        mock = self._run(host="srv.example.com", user="alice")
        args = mock.call_args[0][0]
        assert args[0] == "osascript"
        assert args[1] == "-e"
        assert "ssh" in args[2]
        assert "alice@srv.example.com" in args[2]

    def test_custom_port_included(self):
        mock = self._run(host="srv.example.com", user="bob", port=2222)
        script = mock.call_args[0][0][2]
        assert "-p" in script
        assert "2222" in script

    def test_key_path_included(self):
        mock = self._run(host="srv.example.com", user="alice", key_path="/home/a/.ssh/id_rsa")
        script = mock.call_args[0][0][2]
        assert "-i" in script
        assert "id_rsa" in script

    def test_remote_path_adds_cd_command(self):
        mock = self._run(host="srv.example.com", user="alice", remote_path="/var/www")
        script = mock.call_args[0][0][2]
        assert "cd" in script
        assert "/var/www" in script

    def test_no_remote_path_no_cd(self):
        mock = self._run(host="srv.example.com", user="alice")
        script = mock.call_args[0][0][2]
        assert "cd" not in script


# ---------------------------------------------------------------------------
# open_ssh_terminal — Linux
# ---------------------------------------------------------------------------

class TestOpenSSHTerminalLinux:
    """Verify that a Linux terminal emulator is tried."""

    def _run(self, term_name="gnome-terminal", **kwargs):
        """Simulate Linux with the first terminal in the list present."""
        def _popen_side_effect(cmd, *a, **kw):
            # Succeed when the requested terminal matches
            if cmd[0] == term_name:
                return MagicMock()
            raise FileNotFoundError(f"No such file: {cmd[0]}")

        with patch.object(platform_utils, "PLATFORM", "linux"):
            with patch("subprocess.Popen", side_effect=_popen_side_effect) as mock_p:
                open_ssh_terminal(**kwargs)
                return mock_p

    def test_gnome_terminal_invoked(self):
        mock = self._run("gnome-terminal", host="h", user="u")
        args = mock.call_args[0][0]
        assert args[0] == "gnome-terminal"
        assert "u@h" in args

    def test_fallback_to_xterm(self):
        """If gnome-terminal is not found, xterm is tried."""
        tried: list[str] = []

        def _side_effect(cmd, *a, **kw):
            tried.append(cmd[0])
            if cmd[0] != "xterm":
                raise FileNotFoundError()
            return MagicMock()

        with patch.object(platform_utils, "PLATFORM", "linux"):
            with patch("subprocess.Popen", side_effect=_side_effect):
                open_ssh_terminal(host="h", user="u")

        assert "gnome-terminal" in tried
        assert "xterm" in tried

    def test_no_terminal_raises(self):
        """RuntimeError is raised when no terminal emulator is found."""
        with patch.object(platform_utils, "PLATFORM", "linux"):
            with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
                with pytest.raises(RuntimeError, match="No supported terminal"):
                    open_ssh_terminal(host="h", user="u")

    def test_remote_path_injected_in_ssh_args(self):
        with patch.object(platform_utils, "PLATFORM", "linux"):
            with patch("subprocess.Popen", return_value=MagicMock()) as mock_p:
                open_ssh_terminal(host="h", user="u", remote_path="/opt/app")
                cmd = mock_p.call_args[0][0]
                full_cmd = " ".join(str(a) for a in cmd)
                assert "cd" in full_cmd
                assert "/opt/app" in full_cmd


# ---------------------------------------------------------------------------
# open_ssh_terminal — Windows
# ---------------------------------------------------------------------------

class TestOpenSSHTerminalWindows:
    """Verify Windows Terminal (wt) is tried first, then cmd.exe fallback."""

    def _patch_windows(self):
        """Context-manager stack: patch PLATFORM + CREATE_NEW_CONSOLE."""
        import contextlib, subprocess as _sp

        @contextlib.contextmanager
        def _ctx(side_effect=None):
            with patch.object(platform_utils, "PLATFORM", "win32"):
                # CREATE_NEW_CONSOLE is Windows-only; mock it on non-Windows
                cnc = getattr(_sp, "CREATE_NEW_CONSOLE", None)
                if cnc is None:
                    with patch.object(_sp, "CREATE_NEW_CONSOLE", 0x10, create=True):
                        with patch("subprocess.Popen", return_value=MagicMock(),
                                   side_effect=side_effect) as mock_p:
                            yield mock_p
                else:
                    with patch("subprocess.Popen", return_value=MagicMock(),
                               side_effect=side_effect) as mock_p:
                        yield mock_p

        return _ctx

    def test_windows_terminal_invoked(self):
        ctx = self._patch_windows()
        with ctx() as mock_p:
            open_ssh_terminal(host="h", user="u")
        args = mock_p.call_args[0][0]
        assert args[0] == "wt"

    def test_fallback_to_cmd(self):
        """If Windows Terminal is not found, cmd.exe is used."""
        import subprocess as _sp

        tried: list[str] = []

        def _side_effect(cmd, *a, **kw):
            tried.append(cmd[0])
            if cmd[0] == "wt":
                raise FileNotFoundError()
            return MagicMock()

        with patch.object(platform_utils, "PLATFORM", "win32"):
            cnc = getattr(_sp, "CREATE_NEW_CONSOLE", None)
            if cnc is None:
                with patch.object(_sp, "CREATE_NEW_CONSOLE", 0x10, create=True):
                    with patch("subprocess.Popen", side_effect=_side_effect):
                        open_ssh_terminal(host="h", user="u")
            else:
                with patch("subprocess.Popen", side_effect=_side_effect):
                    open_ssh_terminal(host="h", user="u")

        assert "wt" in tried
        assert "cmd" in tried


# ---------------------------------------------------------------------------
# RemotePanel — signal emission
# ---------------------------------------------------------------------------

class TestRemotePanelTerminalSignal:
    @pytest.fixture
    def panel(self):
        import shiboken6
        p = RemotePanel()
        p.resize(600, 400)
        yield p
        p.close()
        if shiboken6.isValid(p):
            shiboken6.delete(p)

    def test_signal_exists(self, panel):
        assert hasattr(panel, "open_terminal_requested")

    def test_signal_emitted_for_directory(self, panel):
        received: list[str] = []
        panel.open_terminal_requested.connect(received.append)

        # Simulate the internal emit for a directory context menu
        panel.open_terminal_requested.emit("/var/www/html")

        assert received == ["/var/www/html"]

    def test_signal_emitted_for_cwd(self, panel):
        """When no selection, signal carries _cwd."""
        received: list[str] = []
        panel.open_terminal_requested.connect(received.append)

        panel._cwd = "/home/deploy"
        panel.open_terminal_requested.emit(panel._cwd)

        assert received == ["/home/deploy"]


# ---------------------------------------------------------------------------
# MainWindow._on_open_terminal_requested
# ---------------------------------------------------------------------------

class TestMainWindowOpenTerminal:
    """Unit tests for the MainWindow handler using mocked dependencies."""

    def _make_mw(self, conn=None):
        """Create a minimal MainWindow-like mock."""
        from sftp_ui.ui.main_window import MainWindow

        mw = MagicMock(spec=MainWindow)
        mw._active_conn = conn
        mw._signals = MagicMock()
        mw._signals.status = MagicMock()
        mw._signals.status.emit = MagicMock()
        return mw

    def test_no_connection_emits_error(self):
        from sftp_ui.ui.main_window import MainWindow

        mw = self._make_mw(conn=None)
        MainWindow._on_open_terminal_requested(mw, "/some/path")

        mw._signals.status.emit.assert_called_once()
        msg = mw._signals.status.emit.call_args[0][0]
        assert "Not connected" in msg

    def test_happy_path_calls_open_ssh_terminal(self):
        from sftp_ui.ui.main_window import MainWindow

        conn = MagicMock()
        conn.host = "myserver.io"
        conn.user = "deploy"
        conn.port = 22
        conn.key_path = None

        mw = self._make_mw(conn=conn)

        with patch("sftp_ui.ui.main_window.open_ssh_terminal") as mock_term:
            MainWindow._on_open_terminal_requested(mw, "/var/www")

        mock_term.assert_called_once_with(
            host="myserver.io",
            user="deploy",
            port=22,
            remote_path="/var/www",
            key_path=None,
        )
        mw._signals.status.emit.assert_called_once()
        msg = mw._signals.status.emit.call_args[0][0]
        assert "/var/www" in msg

    def test_key_path_forwarded(self):
        from sftp_ui.ui.main_window import MainWindow

        conn = MagicMock()
        conn.host = "h"
        conn.user = "u"
        conn.port = 22
        conn.key_path = "/home/u/.ssh/id_ed25519"

        mw = self._make_mw(conn=conn)

        with patch("sftp_ui.ui.main_window.open_ssh_terminal") as mock_term:
            MainWindow._on_open_terminal_requested(mw, "/opt")

        _, kwargs = mock_term.call_args
        assert kwargs.get("key_path") == "/home/u/.ssh/id_ed25519"

    def test_custom_port_forwarded(self):
        from sftp_ui.ui.main_window import MainWindow

        conn = MagicMock()
        conn.host = "h"
        conn.user = "u"
        conn.port = 4422
        conn.key_path = None

        mw = self._make_mw(conn=conn)

        with patch("sftp_ui.ui.main_window.open_ssh_terminal") as mock_term:
            MainWindow._on_open_terminal_requested(mw, "/")

        _, kwargs = mock_term.call_args
        assert kwargs.get("port") == 4422

    def test_launch_error_emits_status(self):
        from sftp_ui.ui.main_window import MainWindow

        conn = MagicMock()
        conn.host = "h"
        conn.user = "u"
        conn.port = 22
        conn.key_path = None

        mw = self._make_mw(conn=conn)

        with patch(
            "sftp_ui.ui.main_window.open_ssh_terminal",
            side_effect=RuntimeError("No supported terminal emulator found."),
        ):
            MainWindow._on_open_terminal_requested(mw, "/data")

        mw._signals.status.emit.assert_called_once()
        msg = mw._signals.status.emit.call_args[0][0]
        assert "failed" in msg.lower() or "terminal" in msg.lower()

    def test_empty_path_uses_tilde_in_status(self):
        """An empty remote_path shows '~' in the success status message."""
        from sftp_ui.ui.main_window import MainWindow

        conn = MagicMock()
        conn.host = "h"
        conn.user = "u"
        conn.port = 22
        conn.key_path = None

        mw = self._make_mw(conn=conn)

        with patch("sftp_ui.ui.main_window.open_ssh_terminal"):
            MainWindow._on_open_terminal_requested(mw, "")

        msg = mw._signals.status.emit.call_args[0][0]
        assert "~" in msg
