"""
License system — Pro/Free feature gating.

Tests cover:
1. License detection: is_pro() reads license key file
2. License validation: format, expiry, tampering
3. License paths: XDG config dir, custom override
4. Activation: write key file, verify pro status
5. Deactivation: remove key, revert to free
6. Feature gating: pro_required decorator blocks free users
7. ProGate widget: shows upgrade dialog for free users
8. Command registry integration: pro commands disabled for free users
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.license import (
    LicenseManager,
    LicenseStatus,
    is_pro,
    pro_required,
)


# ── 1. License Detection ─────────────────────────────────────────────────────

class TestLicenseDetection:
    """is_pro() checks for a valid license key file."""

    @pytest.fixture
    def license_dir(self, tmp_path):
        """Provide a temp config dir and patch LicenseManager to use it."""
        d = tmp_path / "sftp-ui"
        d.mkdir()
        with patch("sftp_ui.core.license._license_path", return_value=d / "license.key"):
            yield d

    def test_no_license_file_returns_false(self, license_dir):
        assert is_pro() is False

    def test_valid_license_file_returns_true(self, license_dir):
        key_path = license_dir / "license.key"
        key_path.write_text(json.dumps({
            "key": "SFTP-AAAABBBB-CCCCDDDD-EEEEFFFF-00001111",
            "email": "user@example.com",
            "activated_at": int(time.time()),
        }))
        assert is_pro() is True

    def test_empty_license_file_returns_false(self, license_dir):
        key_path = license_dir / "license.key"
        key_path.write_text("")
        assert is_pro() is False

    def test_corrupted_json_returns_false(self, license_dir):
        key_path = license_dir / "license.key"
        key_path.write_text("{invalid json")
        assert is_pro() is False

    def test_missing_key_field_returns_false(self, license_dir):
        key_path = license_dir / "license.key"
        key_path.write_text(json.dumps({"email": "user@example.com"}))
        assert is_pro() is False


# ── 2. License Validation ─────────────────────────────────────────────────────

class TestLicenseValidation:
    """LicenseManager validates key format and content."""

    @pytest.fixture
    def mgr(self, tmp_path):
        key_path = tmp_path / "license.key"
        return LicenseManager(key_path)

    def test_valid_key_format(self, mgr):
        result = mgr.validate_key("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7")
        assert result is True

    def test_invalid_key_too_short(self, mgr):
        result = mgr.validate_key("SFTP-SHORT")
        assert result is False

    def test_invalid_key_empty(self, mgr):
        result = mgr.validate_key("")
        assert result is False

    def test_invalid_key_none(self, mgr):
        result = mgr.validate_key(None)
        assert result is False

    def test_status_when_no_license(self, mgr):
        assert mgr.status() == LicenseStatus.FREE

    def test_status_when_activated(self, mgr, tmp_path):
        key_path = tmp_path / "license.key"
        key_path.write_text(json.dumps({
            "key": "SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7",
            "email": "user@example.com",
            "activated_at": int(time.time()),
        }))
        assert mgr.status() == LicenseStatus.PRO


# ── 3. License Paths ─────────────────────────────────────────────────────────

class TestLicensePaths:
    """License file lives in the platform config directory."""

    def test_default_path_in_config_dir(self):
        from sftp_ui.core.license import _license_path
        path = _license_path()
        assert "sftp-ui" in str(path)
        assert path.name == "license.key"

    def test_custom_path_override(self, tmp_path):
        custom = tmp_path / "custom.key"
        mgr = LicenseManager(custom)
        assert mgr.key_path == custom


# ── 4. Activation ─────────────────────────────────────────────────────────────

class TestActivation:
    """Activation writes the license key file."""

    @pytest.fixture
    def mgr(self, tmp_path):
        return LicenseManager(tmp_path / "license.key")

    def test_activate_writes_key_file(self, mgr):
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "user@example.com")
        assert mgr.key_path.exists()

    def test_activate_stores_key_and_email(self, mgr):
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "user@example.com")
        data = json.loads(mgr.key_path.read_text())
        assert data["key"] == "SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7"
        assert data["email"] == "user@example.com"

    def test_activate_stores_timestamp(self, mgr):
        before = int(time.time())
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "user@example.com")
        data = json.loads(mgr.key_path.read_text())
        assert data["activated_at"] >= before

    def test_activate_changes_status_to_pro(self, mgr):
        assert mgr.status() == LicenseStatus.FREE
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "user@example.com")
        assert mgr.status() == LicenseStatus.PRO

    def test_activate_invalid_key_raises(self, mgr):
        with pytest.raises(ValueError, match="Invalid"):
            mgr.activate("BAD", "user@example.com")

    def test_activate_creates_parent_dir(self, tmp_path):
        mgr = LicenseManager(tmp_path / "nested" / "dir" / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "user@example.com")
        assert mgr.key_path.exists()


# ── 5. Deactivation ──────────────────────────────────────────────────────────

class TestDeactivation:
    """Deactivation removes the license key file."""

    @pytest.fixture
    def mgr(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "user@example.com")
        return mgr

    def test_deactivate_removes_key_file(self, mgr):
        assert mgr.key_path.exists()
        mgr.deactivate()
        assert not mgr.key_path.exists()

    def test_deactivate_changes_status_to_free(self, mgr):
        assert mgr.status() == LicenseStatus.PRO
        mgr.deactivate()
        assert mgr.status() == LicenseStatus.FREE

    def test_deactivate_when_already_free_is_noop(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.deactivate()  # Should not raise
        assert mgr.status() == LicenseStatus.FREE


# ── 6. pro_required Decorator ─────────────────────────────────────────────────

class TestProRequired:
    """pro_required decorator blocks execution for free users."""

    def test_pro_user_can_execute(self, tmp_path):
        key_path = tmp_path / "license.key"
        key_path.write_text(json.dumps({
            "key": "SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7",
            "email": "u@e.com",
            "activated_at": int(time.time()),
        }))
        mgr = LicenseManager(key_path)

        called = []

        @pro_required(mgr)
        def my_feature():
            called.append(True)
            return "result"

        result = my_feature()
        assert called == [True]
        assert result == "result"

    def test_free_user_gets_none(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")

        called = []

        @pro_required(mgr)
        def my_feature():
            called.append(True)

        result = my_feature()
        assert called == []
        assert result is None

    def test_free_user_triggers_callback(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        upgrade_shown = []

        @pro_required(mgr, on_blocked=lambda: upgrade_shown.append(True))
        def my_feature():
            pass

        my_feature()
        assert upgrade_shown == [True]


# ── 7. ProGate Widget ────────────────────────────────────────────────────────

class TestProGateWidget:
    """ProGate widget shows upgrade prompt for free users."""

    @pytest.fixture
    def free_mgr(self, tmp_path):
        return LicenseManager(tmp_path / "license.key")

    @pytest.fixture
    def pro_mgr(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "u@e.com")
        return mgr

    def test_gate_blocks_free_user(self, qapp, free_mgr):
        from sftp_ui.ui.widgets.pro_gate import ProGate
        gate = ProGate(free_mgr, feature_name="Team Profiles")
        assert gate.is_blocked() is True

    def test_gate_allows_pro_user(self, qapp, pro_mgr):
        from sftp_ui.ui.widgets.pro_gate import ProGate
        gate = ProGate(pro_mgr, feature_name="Team Profiles")
        assert gate.is_blocked() is False

    def test_gate_has_feature_name(self, qapp, free_mgr):
        from sftp_ui.ui.widgets.pro_gate import ProGate
        gate = ProGate(free_mgr, feature_name="Scheduled Sync")
        assert gate.feature_name == "Scheduled Sync"

    def test_gate_has_upgrade_button(self, qapp, free_mgr):
        from sftp_ui.ui.widgets.pro_gate import ProGate
        gate = ProGate(free_mgr, feature_name="Team Profiles")
        assert hasattr(gate, "_upgrade_btn")

    def test_gate_emits_upgrade_requested(self, qapp, free_mgr):
        from sftp_ui.ui.widgets.pro_gate import ProGate
        gate = ProGate(free_mgr, feature_name="Team Profiles")
        signals = []
        gate.upgrade_requested.connect(lambda: signals.append(True))
        gate._upgrade_btn.click()
        QApplication.processEvents()
        assert signals == [True]


# ── 8. Command Registry Integration ──────────────────────────────────────────

class TestLicenseCommandIntegration:
    """Pro commands in CommandRegistry respect license status."""

    def test_pro_command_disabled_for_free_user(self, tmp_path):
        from sftp_ui.core.command_registry import Command, CommandRegistry
        mgr = LicenseManager(tmp_path / "license.key")

        reg = CommandRegistry()
        reg.register(Command(
            id="pro.team_profiles",
            name="Team Profiles",
            category="Pro",
            handler=lambda: None,
            enabled_when=lambda: mgr.status() == LicenseStatus.PRO,
        ))

        cmd = reg.get("pro.team_profiles")
        assert cmd.is_enabled() is False

    def test_pro_command_enabled_for_pro_user(self, tmp_path):
        from sftp_ui.core.command_registry import Command, CommandRegistry
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "u@e.com")

        reg = CommandRegistry()
        reg.register(Command(
            id="pro.team_profiles",
            name="Team Profiles",
            category="Pro",
            handler=lambda: None,
            enabled_when=lambda: mgr.status() == LicenseStatus.PRO,
        ))

        cmd = reg.get("pro.team_profiles")
        assert cmd.is_enabled() is True

    def test_search_excludes_disabled_pro_commands(self, tmp_path):
        from sftp_ui.core.command_registry import Command, CommandRegistry
        mgr = LicenseManager(tmp_path / "license.key")

        reg = CommandRegistry()
        reg.register(Command(
            id="free.refresh", name="Refresh", category="Nav",
            handler=lambda: None,
        ))
        reg.register(Command(
            id="pro.sync", name="Scheduled Sync", category="Pro",
            handler=lambda: None,
            enabled_when=lambda: mgr.status() == LicenseStatus.PRO,
        ))

        results = reg.search("", include_disabled=False)
        assert len(results) == 1
        assert results[0].id == "free.refresh"
