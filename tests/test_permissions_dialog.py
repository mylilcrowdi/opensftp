"""
Tests for PermissionsDialog (chmod editor) and related helpers.

Covers:
- mode_to_symbolic() correctness (standard, special bits)
- Dialog initialises checkboxes correctly from a given mode
- current_mode() returns the correct integer after checkbox changes
- Octal field reflects checkbox state
- Symbolic label reflects mode
- Checking/unchecking a checkbox updates octal + symbolic
- Octal input updates checkboxes + symbolic label
- Octal validator accepts only valid octal digits (0-7)
- _do_permissions() wires up the dialog and calls sftp.chmod
- st_mode is populated by SFTPClient.listdir (unit-level mock)
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch, call

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from PySide6.QtWidgets import QDialog
from sftp_ui.ui.dialogs.permissions_dialog import (
    PermissionsDialog,
    mode_to_symbolic,
    _OctalValidator,
    _RWX_ROWS,
    _SPECIAL_BITS,
)
from sftp_ui.core.sftp_client import RemoteEntry

_ACCEPTED = QDialog.DialogCode.Accepted
_REJECTED = QDialog.DialogCode.Rejected


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def dlg(qapp):
    d = PermissionsDialog(path="/home/user/file.txt", name="file.txt", initial_mode=0o644)
    yield d
    d.close()


# ── mode_to_symbolic ───────────────────────────────────────────────────────────

class TestModeToSymbolic:
    def test_0644(self):
        assert mode_to_symbolic(0o644) == "rw-r--r--"

    def test_0755(self):
        assert mode_to_symbolic(0o755) == "rwxr-xr-x"

    def test_0700(self):
        assert mode_to_symbolic(0o700) == "rwx------"

    def test_0000(self):
        assert mode_to_symbolic(0o000) == "---------"

    def test_0777(self):
        assert mode_to_symbolic(0o777) == "rwxrwxrwx"

    def test_0444(self):
        assert mode_to_symbolic(0o444) == "r--r--r--"

    def test_suid_with_execute(self):
        # SUID + owner execute → 's' in position 2
        sym = mode_to_symbolic(0o4755)
        assert sym[2] == "s"

    def test_suid_without_execute(self):
        # SUID but no owner execute → 'S' in position 2
        sym = mode_to_symbolic(0o4644)
        assert sym[2] == "S"

    def test_sgid_with_execute(self):
        # SGID + group execute → 's' in position 5
        sym = mode_to_symbolic(0o2755)
        assert sym[5] == "s"

    def test_sgid_without_execute(self):
        sym = mode_to_symbolic(0o2644)
        assert sym[5] == "S"

    def test_sticky_with_execute(self):
        # Sticky + other execute → 't' in position 8
        sym = mode_to_symbolic(0o1755)
        assert sym[8] == "t"

    def test_sticky_without_execute(self):
        sym = mode_to_symbolic(0o1644)
        assert sym[8] == "T"

    def test_length_always_9(self):
        for mode in (0o000, 0o777, 0o644, 0o4755, 0o2644, 0o1755):
            assert len(mode_to_symbolic(mode)) == 9


# ── dialog initialisation ──────────────────────────────────────────────────────

class TestPermissionsDialogInit:
    def test_initial_mode_0644(self, dlg):
        """0644: owner read+write, group+other read only."""
        # Read row
        assert dlg._cb[0][0].isChecked()   # owner read
        assert dlg._cb[0][1].isChecked()   # group read
        assert dlg._cb[0][2].isChecked()   # other read
        # Write row
        assert dlg._cb[1][0].isChecked()   # owner write
        assert not dlg._cb[1][1].isChecked()
        assert not dlg._cb[1][2].isChecked()
        # Execute row
        assert not dlg._cb[2][0].isChecked()
        assert not dlg._cb[2][1].isChecked()
        assert not dlg._cb[2][2].isChecked()

    def test_octal_shows_0644(self, dlg):
        assert dlg._octal_edit.text() == "0644"

    def test_symbolic_shows_rw_r__r__(self, dlg):
        assert dlg._sym_lbl.text() == "rw-r--r--"

    def test_no_special_bits_set(self, dlg):
        for cb in dlg._special_cb:
            assert not cb.isChecked()

    def test_path_attribute(self, dlg):
        assert dlg.path == "/home/user/file.txt"

    def test_window_title_contains_name(self, dlg):
        assert "file.txt" in dlg.windowTitle()

    def test_zero_mode_initialises_cleanly(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0)
        assert d._octal_edit.text() == "0000"
        assert d._sym_lbl.text() == "---------"
        d.close()

    def test_mode_stripped_to_permission_bits(self, qapp):
        # Pass a raw st_mode that includes file-type bits (regular file = 0o100644)
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o100644)
        assert d._octal_edit.text() == "0644"
        d.close()


# ── current_mode() ─────────────────────────────────────────────────────────────

class TestCurrentMode:
    def test_returns_0644_on_init(self, dlg):
        assert dlg.current_mode() == 0o644

    def test_set_execute_owner(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        d._cb[2][0].setChecked(True)   # owner execute
        assert d.current_mode() == 0o744
        d.close()

    def test_clear_all_gives_zero(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o777)
        for row in d._cb:
            for cb in row:
                cb.setChecked(False)
        for cb in d._special_cb:
            cb.setChecked(False)
        assert d.current_mode() == 0o000
        d.close()

    def test_suid_bit(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o755)
        d._special_cb[0].setChecked(True)   # SUID
        assert d.current_mode() & 0o4000
        d.close()

    def test_sgid_bit(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o755)
        d._special_cb[1].setChecked(True)   # SGID
        assert d.current_mode() & 0o2000
        d.close()

    def test_sticky_bit(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o755)
        d._special_cb[2].setChecked(True)   # Sticky
        assert d.current_mode() & 0o1000
        d.close()


# ── checkbox → octal / symbolic sync ──────────────────────────────────────────

class TestCheckboxSync:
    def test_toggling_checkbox_updates_octal(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        # Enable owner execute
        d._cb[2][0].setChecked(True)
        assert d._octal_edit.text() == "0744"
        d.close()

    def test_toggling_checkbox_updates_symbolic(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        d._cb[2][0].setChecked(True)
        assert d._sym_lbl.text() == "rwxr--r--"
        d.close()

    def test_special_suid_updates_symbolic(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o755)
        d._special_cb[0].setChecked(True)
        sym = d._sym_lbl.text()
        assert sym[2] == "s"
        d.close()


# ── octal input → checkbox / symbolic sync ────────────────────────────────────

class TestOctalSync:
    def test_typing_0755_updates_checkboxes(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        d._on_octal_edited("0755")
        # owner: rwx
        assert d._cb[0][0].isChecked()
        assert d._cb[1][0].isChecked()
        assert d._cb[2][0].isChecked()
        # group: r-x
        assert d._cb[0][1].isChecked()
        assert not d._cb[1][1].isChecked()
        assert d._cb[2][1].isChecked()
        d.close()

    def test_typing_0755_updates_symbolic(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        d._on_octal_edited("0755")
        assert d._sym_lbl.text() == "rwxr-xr-x"
        d.close()

    def test_partial_octal_does_not_crash(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        # Partial input — should not raise
        d._on_octal_edited("07")
        d.close()

    def test_empty_octal_does_not_crash(self, qapp):
        d = PermissionsDialog(path="/tmp/x", name="x", initial_mode=0o644)
        d._on_octal_edited("")
        d.close()


# ── _OctalValidator ────────────────────────────────────────────────────────────

class TestOctalValidator:
    @pytest.fixture
    def validator(self, qapp):
        from PySide6.QtWidgets import QLineEdit
        edit = QLineEdit()
        return _OctalValidator(0, 0o7777, edit)

    def test_valid_octal_string(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("0755", 4)
        assert state == QValidator.State.Acceptable

    def test_invalid_digit_8(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("0785", 4)
        assert state == QValidator.State.Invalid

    def test_invalid_digit_9(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("9", 1)
        assert state == QValidator.State.Invalid

    def test_too_long(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("01234", 5)
        assert state == QValidator.State.Invalid

    def test_empty_is_intermediate(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("", 0)
        assert state == QValidator.State.Intermediate

    def test_four_zeros(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("0000", 4)
        assert state == QValidator.State.Acceptable

    def test_7777(self, validator):
        from PySide6.QtGui import QValidator
        state, _, _ = validator.validate("7777", 4)
        assert state == QValidator.State.Acceptable


# ── RemotePanel._do_permissions ────────────────────────────────────────────────

class TestDoPerm:
    """Tests for RemotePanel._do_permissions() via mock sftp."""

    @pytest.fixture
    def panel(self, qapp):
        import gc
        from sftp_ui.ui.panels.remote_panel import RemotePanel
        p = RemotePanel()
        yield p
        p._skeleton._anim.stop()
        p.close()
        p.deleteLater()
        gc.collect()
        QApplication.processEvents()

    def _entry(self, name: str, st_mode: int = 0o100644) -> RemoteEntry:
        return RemoteEntry(
            name=name, path=f"/remote/{name}",
            is_dir=False, size=100, mtime=0,
            st_mode=st_mode,
        )

    def test_chmod_called_on_accept(self, panel, qapp):
        """When dialog is accepted, sftp.chmod is called with the chosen mode."""
        import threading as _threading

        mock_sftp = MagicMock()
        panel._sftp = mock_sftp
        entry = self._entry("script.sh", st_mode=0o100644)

        spawned_threads: list[_threading.Thread] = []
        original_thread_cls = _threading.Thread

        def capturing_thread(*args, target=None, daemon=None, **kwargs):
            t = original_thread_cls(target=target, daemon=daemon, **kwargs)
            spawned_threads.append(t)
            return t

        # Patch PermissionsDialog.exec to simulate Accepted with mode 0o755
        with patch(
            "sftp_ui.ui.panels.remote_panel.PermissionsDialog"
        ) as MockDlg, patch.object(
            _threading, "Thread",
            side_effect=capturing_thread,
        ):
            instance = MagicMock()
            instance.exec.return_value = _ACCEPTED
            instance.current_mode.return_value = 0o755
            MockDlg.return_value = instance

            panel._do_permissions(entry)

        # Join so the background work actually runs before we assert.
        # Threads were already started by _do_permissions via .start()
        for t in spawned_threads:
            t.join(timeout=2.0)

        mock_sftp.chmod.assert_called_once_with("/remote/script.sh", 0o755)

    def test_chmod_not_called_on_cancel(self, panel, qapp):
        """When dialog is rejected, sftp.chmod must NOT be called."""
        mock_sftp = MagicMock()
        panel._sftp = mock_sftp
        entry = self._entry("readme.txt", st_mode=0o100644)

        with patch(
            "sftp_ui.ui.panels.remote_panel.PermissionsDialog"
        ) as MockDlg:
            instance = MagicMock()
            instance.exec.return_value = _REJECTED
            MockDlg.return_value = instance

            panel._do_permissions(entry)

        mock_sftp.chmod.assert_not_called()

    def test_unchanged_mode_skips_chmod(self, panel, qapp):
        """If the user accepts but doesn't change the mode, chmod is skipped."""
        mock_sftp = MagicMock()
        panel._sftp = mock_sftp
        entry = self._entry("readme.txt", st_mode=0o100644)

        with patch(
            "sftp_ui.ui.panels.remote_panel.PermissionsDialog"
        ) as MockDlg:
            instance = MagicMock()
            instance.exec.return_value = _ACCEPTED
            # Same mode as entry.st_mode & 0o7777 = 0o644
            instance.current_mode.return_value = 0o644
            MockDlg.return_value = instance

            panel._do_permissions(entry)

        mock_sftp.chmod.assert_not_called()

    def test_stat_called_when_st_mode_is_zero(self, panel, qapp):
        """When entry.st_mode == 0, a live stat() should be attempted."""
        mock_sftp = MagicMock()
        fake_attrs = MagicMock()
        fake_attrs.st_mode = 0o100755
        mock_sftp.stat.return_value = fake_attrs
        panel._sftp = mock_sftp

        entry = self._entry("binary", st_mode=0)

        with patch(
            "sftp_ui.ui.panels.remote_panel.PermissionsDialog"
        ) as MockDlg:
            instance = MagicMock()
            instance.exec.return_value = _REJECTED
            MockDlg.return_value = instance

            panel._do_permissions(entry)

        mock_sftp.stat.assert_called_once_with("/remote/binary")


# ── st_mode field propagation in SFTPClient.listdir ───────────────────────────

class TestStModeInListdir:
    """Verify that listdir() + listdir_stream() populate st_mode on RemoteEntry."""

    def test_listdir_populates_st_mode(self):
        from sftp_ui.core.sftp_client import SFTPClient
        import stat as _stat_mod

        client = SFTPClient.__new__(SFTPClient)

        fake_attr = MagicMock()
        fake_attr.filename = "test.py"
        fake_attr.st_mode = 0o100644
        fake_attr.st_size = 512
        fake_attr.st_mtime = 0

        mock_sftp = MagicMock()
        mock_sftp.listdir_attr.return_value = [fake_attr]
        client._sftp = mock_sftp

        entries = client.listdir("/tmp")
        assert len(entries) == 1
        assert entries[0].st_mode == 0o100644

    def test_listdir_stream_populates_st_mode(self):
        from sftp_ui.core.sftp_client import SFTPClient

        client = SFTPClient.__new__(SFTPClient)

        fake_attr = MagicMock()
        fake_attr.filename = "data.csv"
        fake_attr.st_mode = 0o100755
        fake_attr.st_size = 1024
        fake_attr.st_mtime = 0

        mock_sftp = MagicMock()
        mock_sftp.listdir_iter.return_value = iter([fake_attr])
        client._sftp = mock_sftp

        collected: list = []

        def on_batch(batch, is_final):
            collected.extend(batch)

        client.listdir_stream("/tmp", on_batch)
        assert len(collected) == 1
        assert collected[0].st_mode == 0o100755

    def test_st_mode_default_zero(self):
        """RemoteEntry created without st_mode defaults to 0."""
        e = RemoteEntry(name="x", path="/x", is_dir=False, size=0, mtime=0)
        assert e.st_mode == 0
