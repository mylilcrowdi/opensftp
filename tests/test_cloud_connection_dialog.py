"""
Tests for the cloud-protocol additions to ConnectionDialog.

Covers: protocol combo visibility, cloud field population/validation,
        S3 connection round-trip, SFTP backward-compat.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.connection import CloudConfig, Connection
from sftp_ui.ui.dialogs.connection_dialog import (
    ConnectionDialog,
    _PROTOCOL_IDS,
    _PROTOCOL_LABELS,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _s3_conn(**kw) -> Connection:
    cloud = CloudConfig(
        provider="s3",
        bucket=kw.pop("bucket", "test-bucket"),
        region=kw.pop("region", "us-east-1"),
        access_key=kw.pop("access_key", "AK"),
        secret_key=kw.pop("secret_key", "SK"),
        endpoint_url=kw.pop("endpoint_url", ""),
        prefix=kw.pop("prefix", ""),
    )
    return Connection(name=kw.pop("name", "My S3"), protocol="s3", cloud=cloud, **kw)


def _gcs_conn(**kw) -> Connection:
    cloud = CloudConfig(provider="gcs", bucket=kw.pop("bucket", "gcs-bucket"))
    return Connection(name=kw.pop("name", "My GCS"), protocol="gcs", cloud=cloud, **kw)


def _sftp_conn(**kw) -> Connection:
    defaults = dict(name="SFTP", host="10.0.0.1", user="admin", port=22, password="pw")
    defaults.update(kw)
    return Connection(**defaults)


# ════════════════════════════════════════════════════════════════════════════
# Protocol combo metadata
# ════════════════════════════════════════════════════════════════════════════

class TestProtocolComboMetadata:
    def test_sftp_is_first_protocol(self):
        assert _PROTOCOL_IDS[0] == "sftp"

    def test_s3_in_protocol_list(self):
        assert "s3" in _PROTOCOL_IDS

    def test_gcs_in_protocol_list(self):
        assert "gcs" in _PROTOCOL_IDS

    def test_labels_count_matches_ids(self):
        assert len(_PROTOCOL_IDS) == len(_PROTOCOL_LABELS)


# ── Helper: check a widget's own visibility flag (not ancestors) ──────────────
# isVisible() returns False for unshown dialogs even if the child is "visible".
# isHidden() only reflects the widget's own explicit hidden state.
def _visible(widget) -> bool:
    return not widget.isHidden()


# ════════════════════════════════════════════════════════════════════════════
# Default state (new connection)
# ════════════════════════════════════════════════════════════════════════════

class TestConnectionDialogDefaultState:
    def test_default_protocol_is_sftp(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._current_protocol() == "sftp"

    def test_sftp_group_visible_by_default(self, qapp):
        dlg = ConnectionDialog()
        assert _visible(dlg._sftp_group)

    def test_cloud_group_hidden_by_default(self, qapp):
        dlg = ConnectionDialog()
        assert not _visible(dlg._cloud_group)

    def test_protocol_combo_has_all_protocols(self, qapp):
        dlg = ConnectionDialog()
        count = dlg._protocol_combo.count()
        assert count == len(_PROTOCOL_IDS)


# ════════════════════════════════════════════════════════════════════════════
# Protocol switching via combo
# ════════════════════════════════════════════════════════════════════════════

class TestProtocolSwitching:
    def _select_protocol(self, dlg: ConnectionDialog, protocol: str) -> None:
        idx = _PROTOCOL_IDS.index(protocol)
        dlg._protocol_combo.setCurrentIndex(idx)

    def test_select_s3_hides_sftp_group(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "s3")
        assert not _visible(dlg._sftp_group)

    def test_select_s3_shows_cloud_group(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "s3")
        assert _visible(dlg._cloud_group)

    def test_select_gcs_hides_sftp_group(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "gcs")
        assert not _visible(dlg._sftp_group)

    def test_select_gcs_shows_cloud_group(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "gcs")
        assert _visible(dlg._cloud_group)

    def test_switch_back_to_sftp_shows_sftp_group(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "s3")
        self._select_protocol(dlg, "sftp")
        assert _visible(dlg._sftp_group)
        assert not _visible(dlg._cloud_group)

    def test_s3_cloud_group_title(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "s3")
        assert "S3" in dlg._cloud_group.title()

    def test_gcs_cloud_group_title(self, qapp):
        dlg = ConnectionDialog()
        self._select_protocol(dlg, "gcs")
        assert "Google" in dlg._cloud_group.title() or "GCS" in dlg._cloud_group.title()


# ════════════════════════════════════════════════════════════════════════════
# Populate from existing connection
# ════════════════════════════════════════════════════════════════════════════

class TestConnectionDialogPopulate:
    def test_populate_s3_sets_protocol_combo(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn())
        assert dlg._current_protocol() == "s3"

    def test_populate_s3_sets_bucket(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(bucket="prod-data"))
        assert dlg._cloud_bucket.text() == "prod-data"

    def test_populate_s3_sets_region(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(region="eu-west-1"))
        assert dlg._cloud_region.text() == "eu-west-1"

    def test_populate_s3_sets_access_key(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(access_key="MYKEY"))
        assert dlg._cloud_access_key.text() == "MYKEY"

    def test_populate_s3_sets_secret_key(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(secret_key="MySECRET"))
        assert dlg._cloud_secret_key.text() == "MySECRET"

    def test_populate_s3_sets_endpoint_url(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(endpoint_url="https://minio.example.com"))
        assert dlg._cloud_endpoint_url.text() == "https://minio.example.com"

    def test_populate_s3_sets_prefix(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(prefix="backups/"))
        assert dlg._cloud_prefix.text() == "backups/"

    def test_populate_s3_shows_cloud_group(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn())
        assert _visible(dlg._cloud_group)

    def test_populate_s3_hides_sftp_group(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn())
        assert not _visible(dlg._sftp_group)

    def test_populate_gcs_sets_protocol_combo(self, qapp):
        dlg = ConnectionDialog(conn=_gcs_conn())
        assert dlg._current_protocol() == "gcs"

    def test_populate_sftp_still_works(self, qapp):
        dlg = ConnectionDialog(conn=_sftp_conn(host="192.168.1.5"))
        assert dlg._current_protocol() == "sftp"
        assert dlg._host.text() == "192.168.1.5"
        assert _visible(dlg._sftp_group)

    def test_populate_preserves_name(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(name="Prod Bucket"))
        assert dlg._name.text() == "Prod Bucket"

    def test_populate_preserves_group(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(group="Cloud"))
        assert dlg._group.text() == "Cloud"

    def test_populate_preserves_favorite(self, qapp):
        dlg = ConnectionDialog(conn=_s3_conn(favorite=True))
        assert dlg._favorite.isChecked()


# ════════════════════════════════════════════════════════════════════════════
# Validation — cloud fields
# ════════════════════════════════════════════════════════════════════════════

class TestCloudValidation:
    def _select_s3(self, dlg: ConnectionDialog) -> None:
        idx = _PROTOCOL_IDS.index("s3")
        dlg._protocol_combo.setCurrentIndex(idx)

    def test_missing_name_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._select_s3(dlg)
        dlg._name.setText("")
        dlg._cloud_bucket.setText("b")
        dlg._on_accept()
        assert dlg._error_label.text() != ""
        assert dlg._result_conn is None

    def test_missing_bucket_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._select_s3(dlg)
        dlg._name.setText("MyS3")
        dlg._cloud_bucket.setText("")
        dlg._on_accept()
        assert dlg._error_label.text() != ""
        assert dlg._result_conn is None

    def test_valid_s3_fields_produce_connection(self, qapp):
        dlg = ConnectionDialog()
        self._select_s3(dlg)
        dlg._name.setText("My S3")
        dlg._cloud_bucket.setText("my-bucket")
        dlg._cloud_region.setText("us-east-1")
        dlg._cloud_access_key.setText("AKID")
        dlg._cloud_secret_key.setText("SK")
        dlg._on_accept()
        assert dlg._result_conn is not None
        conn = dlg._result_conn
        assert conn.protocol == "s3"
        assert conn.cloud is not None
        assert conn.cloud.bucket == "my-bucket"

    def test_s3_endpoint_url_optional(self, qapp):
        dlg = ConnectionDialog()
        self._select_s3(dlg)
        dlg._name.setText("MinIO")
        dlg._cloud_bucket.setText("data")
        dlg._cloud_endpoint_url.setText("")
        dlg._on_accept()
        assert dlg._result_conn is not None
        assert dlg._result_conn.cloud.endpoint_url == ""

    def test_s3_endpoint_url_stored(self, qapp):
        dlg = ConnectionDialog()
        self._select_s3(dlg)
        dlg._name.setText("MinIO")
        dlg._cloud_bucket.setText("data")
        dlg._cloud_endpoint_url.setText("https://minio.example.com")
        dlg._on_accept()
        assert dlg._result_conn.cloud.endpoint_url == "https://minio.example.com"

    def test_edit_preserves_id(self, qapp):
        original = _s3_conn()
        dlg = ConnectionDialog(conn=original)
        dlg._name.setText("Updated Name")
        dlg._on_accept()
        assert dlg._result_conn is not None
        assert dlg._result_conn.id == original.id

    def test_new_connection_gets_uuid(self, qapp):
        dlg = ConnectionDialog()
        self._select_s3(dlg)
        dlg._name.setText("New S3")
        dlg._cloud_bucket.setText("bucket")
        dlg._on_accept()
        assert dlg._result_conn is not None
        import uuid
        # Should not raise
        uuid.UUID(dlg._result_conn.id)


# ════════════════════════════════════════════════════════════════════════════
# Backward compatibility — SFTP fields unchanged
# ════════════════════════════════════════════════════════════════════════════

class TestSFTPBackwardCompat:
    def test_sftp_name_required(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("")
        dlg._host.setText("h")
        dlg._user.setText("u")
        dlg._on_accept()
        assert dlg._result_conn is None

    def test_sftp_host_required(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("n")
        dlg._host.setText("")
        dlg._user.setText("u")
        dlg._on_accept()
        assert dlg._result_conn is None

    def test_sftp_user_required(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("n")
        dlg._host.setText("h")
        dlg._user.setText("")
        dlg._on_accept()
        assert dlg._result_conn is None

    def test_sftp_valid_produces_sftp_protocol(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("Server")
        dlg._host.setText("10.0.0.1")
        dlg._user.setText("root")
        dlg._password.setText("pw")
        dlg._on_accept()
        assert dlg._result_conn is not None
        assert dlg._result_conn.protocol == "sftp"
        assert dlg._result_conn.cloud is None
