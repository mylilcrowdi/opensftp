"""
License activation dialog — enter key, activate, deactivate.

Tests cover:
1. Dialog UI: key input field, activate button, status label
2. Activate with valid key: writes license, shows Pro status
3. Activate with invalid key: shows error, stays Free
4. Deactivate: removes license, shows Free status
5. Pre-filled status when already Pro
6. Upgrade link opens browser
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sftp_ui.core.license import LicenseManager, LicenseStatus
from sftp_ui.ui.dialogs.license_dialog import LicenseDialog


# ── 1. Dialog UI ──────────────────────────────────────────────────────────────

class TestLicenseDialogUI:
    @pytest.fixture
    def dialog(self, qapp, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        d = LicenseDialog(mgr)
        yield d
        d.close()

    def test_has_key_input(self, dialog):
        assert hasattr(dialog, "_key_input")
        assert dialog._key_input.placeholderText() != ""

    def test_has_activate_button(self, dialog):
        assert hasattr(dialog, "_activate_btn")

    def test_has_status_label(self, dialog):
        assert hasattr(dialog, "_status_label")

    def test_has_buy_link(self, dialog):
        assert hasattr(dialog, "_buy_btn")

    def test_free_status_shown_initially(self, dialog):
        assert "Free" in dialog._status_label.text()

    def test_key_input_accepts_text(self, dialog):
        dialog._key_input.setText("PRO-ABCD-1234-EFGH")
        assert dialog._key_input.text() == "PRO-ABCD-1234-EFGH"


# ── 2. Activate Valid Key ─────────────────────────────────────────────────────

class TestActivateValidKey:
    @pytest.fixture
    def dialog(self, qapp, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        d = LicenseDialog(mgr)
        yield d
        d.close()

    def test_activate_valid_key_shows_pro(self, dialog, qapp):
        dialog._key_input.setText("PRO-ABCD-1234-EFGH")
        dialog._activate_btn.click()
        QApplication.processEvents()
        assert "Pro" in dialog._status_label.text()

    def test_activate_writes_license_file(self, dialog, qapp):
        dialog._key_input.setText("PRO-ABCD-1234-EFGH")
        dialog._activate_btn.click()
        assert dialog._manager.status() == LicenseStatus.PRO

    def test_activate_disables_input(self, dialog, qapp):
        dialog._key_input.setText("PRO-ABCD-1234-EFGH")
        dialog._activate_btn.click()
        QApplication.processEvents()
        assert not dialog._key_input.isEnabled()

    def test_activate_shows_deactivate_button(self, dialog, qapp):
        dialog._key_input.setText("PRO-ABCD-1234-EFGH")
        dialog._activate_btn.click()
        QApplication.processEvents()
        assert hasattr(dialog, "_deactivate_btn")
        assert not dialog._deactivate_btn.isHidden()


# ── 3. Activate Invalid Key ──────────────────────────────────────────────────

class TestActivateInvalidKey:
    @pytest.fixture
    def dialog(self, qapp, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        d = LicenseDialog(mgr)
        yield d
        d.close()

    def test_invalid_key_shows_error(self, dialog, qapp):
        dialog._key_input.setText("INVALID-KEY")
        dialog._activate_btn.click()
        QApplication.processEvents()
        assert "Invalid" in dialog._error_label.text() or "invalid" in dialog._error_label.text().lower()

    def test_invalid_key_stays_free(self, dialog, qapp):
        dialog._key_input.setText("BAD")
        dialog._activate_btn.click()
        assert dialog._manager.status() == LicenseStatus.FREE

    def test_empty_key_shows_error(self, dialog, qapp):
        dialog._key_input.setText("")
        dialog._activate_btn.click()
        QApplication.processEvents()
        text = dialog._error_label.text().lower()
        assert "invalid" in text or "enter" in text or "empty" in text


# ── 4. Deactivate ────────────────────────────────────────────────────────────

class TestDeactivate:
    @pytest.fixture
    def dialog(self, qapp, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("PRO-ABCD-1234-EFGH", "u@e.com")
        d = LicenseDialog(mgr)
        yield d
        d.close()

    def test_deactivate_shows_free(self, dialog, qapp):
        dialog._deactivate_btn.click()
        QApplication.processEvents()
        assert "Free" in dialog._status_label.text()

    def test_deactivate_removes_license(self, dialog, qapp):
        dialog._deactivate_btn.click()
        assert dialog._manager.status() == LicenseStatus.FREE

    def test_deactivate_re_enables_input(self, dialog, qapp):
        dialog._deactivate_btn.click()
        QApplication.processEvents()
        assert dialog._key_input.isEnabled()


# ── 5. Pre-filled Pro Status ─────────────────────────────────────────────────

class TestPrefilledPro:
    def test_pro_status_shown_on_open(self, qapp, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("PRO-ABCD-1234-EFGH", "u@e.com")
        dialog = LicenseDialog(mgr)
        assert "Pro" in dialog._status_label.text()
        assert not dialog._key_input.isEnabled()
        dialog.close()


# ── 6. Buy Link ──────────────────────────────────────────────────────────────

class TestBuyLink:
    def test_buy_button_opens_url(self, qapp, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        dialog = LicenseDialog(mgr)
        with patch("sftp_ui.ui.dialogs.license_dialog.QDesktopServices") as mock_ds:
            dialog._buy_btn.click()
            QApplication.processEvents()
            mock_ds.openUrl.assert_called_once()
        dialog.close()
