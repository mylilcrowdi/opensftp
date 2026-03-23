"""
Tests for cloud_client.py — S3Client, GCSClient, make_cloud_client, and the
CloudConfig / Connection model extensions.

All boto3 calls are mocked; no real AWS credentials are required.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sftp_ui.core.connection import CloudConfig, Connection
from sftp_ui.core.cloud_client import (
    CloudAuthError,
    CloudConnectionError,
    CloudOperationError,
    CloudProviderNotInstalled,
    GCSClient,
    S3Client,
    make_cloud_client,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _s3_conn(**kw) -> Connection:
    cloud = CloudConfig(
        provider="s3",
        bucket=kw.pop("bucket", "my-bucket"),
        region=kw.pop("region", "us-east-1"),
        access_key=kw.pop("access_key", "AKID"),
        secret_key=kw.pop("secret_key", "secret"),
        endpoint_url=kw.pop("endpoint_url", ""),
        prefix=kw.pop("prefix", ""),
    )
    return Connection(name="S3 Test", protocol="s3", cloud=cloud, **kw)


def _make_s3_client(conn: Connection | None = None) -> tuple[S3Client, MagicMock]:
    """Return (S3Client, mock_boto3_client) with connect() already called."""
    conn = conn or _s3_conn()
    mock_s3 = MagicMock()

    with patch("boto3.client", return_value=mock_s3) as _mock_factory:
        client = S3Client()
        client.connect(conn)

    return client, mock_s3


# ════════════════════════════════════════════════════════════════════════════
# CloudConfig dataclass
# ════════════════════════════════════════════════════════════════════════════

class TestCloudConfig:
    def test_valid_s3(self):
        cfg = CloudConfig(provider="s3", bucket="b")
        assert cfg.provider == "s3"
        assert cfg.bucket == "b"

    def test_valid_gcs(self):
        cfg = CloudConfig(provider="gcs", bucket="b")
        assert cfg.provider == "gcs"

    def test_invalid_provider(self):
        with pytest.raises(ValueError, match="provider"):
            CloudConfig(provider="ftp", bucket="b")

    def test_empty_bucket(self):
        with pytest.raises(ValueError, match="bucket"):
            CloudConfig(provider="s3", bucket="")

    def test_to_dict_round_trip(self):
        cfg = CloudConfig(
            provider="s3", bucket="test-bkt",
            region="eu-west-1", access_key="AK",
            secret_key="SK", endpoint_url="https://s3.example.com",
            prefix="data/",
        )
        d = cfg.to_dict()
        restored = CloudConfig.from_dict(d)
        assert restored == cfg

    def test_from_dict_ignores_unknown_keys(self):
        d = {"provider": "s3", "bucket": "b", "unknown_field": "x"}
        cfg = CloudConfig.from_dict(d)
        assert cfg.bucket == "b"

    def test_defaults(self):
        cfg = CloudConfig(provider="s3", bucket="b")
        assert cfg.region == ""
        assert cfg.access_key == ""
        assert cfg.secret_key == ""
        assert cfg.endpoint_url == ""
        assert cfg.prefix == ""


# ════════════════════════════════════════════════════════════════════════════
# Connection with cloud protocol
# ════════════════════════════════════════════════════════════════════════════

class TestConnectionCloudProtocol:
    def test_s3_connection_valid(self):
        conn = _s3_conn()
        assert conn.protocol == "s3"
        assert conn.cloud is not None
        assert conn.cloud.bucket == "my-bucket"

    def test_gcs_connection_valid(self):
        cloud = CloudConfig(provider="gcs", bucket="my-gcs-bucket")
        conn = Connection(name="GCS", protocol="gcs", cloud=cloud)
        assert conn.protocol == "gcs"

    def test_cloud_connection_requires_cloud_config(self):
        with pytest.raises(ValueError, match="cloud config required"):
            Connection(name="S3", protocol="s3", cloud=None)

    def test_sftp_connection_still_requires_host(self):
        with pytest.raises(ValueError, match="host must not be empty"):
            Connection(name="Bad", protocol="sftp", host="", user="u")

    def test_sftp_connection_still_requires_user(self):
        with pytest.raises(ValueError, match="user must not be empty"):
            Connection(name="Bad", protocol="sftp", host="h", user="")

    def test_connection_to_dict_includes_cloud(self):
        conn = _s3_conn()
        d = conn.to_dict()
        assert d["protocol"] == "s3"
        assert d["cloud"]["bucket"] == "my-bucket"

    def test_connection_from_dict_round_trip(self):
        conn = _s3_conn()
        d = conn.to_dict()
        restored = Connection.from_dict(d)
        assert restored.protocol == "s3"
        assert restored.cloud is not None
        assert restored.cloud.bucket == "my-bucket"
        assert restored.cloud.region == "us-east-1"

    def test_connection_from_dict_sftp_still_works(self):
        """Existing SFTP connections without 'protocol' key default to 'sftp'."""
        d = {
            "name": "Legacy", "host": "10.0.0.1",
            "user": "root", "port": 22,
            "id": "test-id",
        }
        conn = Connection.from_dict(d)
        assert conn.protocol == "sftp"
        assert conn.host == "10.0.0.1"


# ════════════════════════════════════════════════════════════════════════════
# make_cloud_client factory
# ════════════════════════════════════════════════════════════════════════════

class TestMakeCloudClient:
    def test_s3_protocol_returns_s3_client(self):
        conn = _s3_conn()
        client = make_cloud_client(conn)
        assert isinstance(client, S3Client)

    def test_gcs_protocol_returns_gcs_client(self):
        cloud = CloudConfig(provider="gcs", bucket="b")
        conn = Connection(name="G", protocol="gcs", cloud=cloud)
        client = make_cloud_client(conn)
        assert isinstance(client, GCSClient)

    def test_sftp_protocol_raises(self):
        conn = Connection(name="S", host="h", user="u", protocol="sftp")
        with pytest.raises(ValueError, match="non-cloud protocol"):
            make_cloud_client(conn)


# ════════════════════════════════════════════════════════════════════════════
# S3Client — connect()
# ════════════════════════════════════════════════════════════════════════════

class TestS3ClientConnect:
    def test_connect_calls_head_bucket(self):
        conn = _s3_conn()
        mock_s3 = MagicMock()
        with patch("boto3.client", return_value=mock_s3):
            client = S3Client()
            client.connect(conn)
        mock_s3.head_bucket.assert_called_once_with(Bucket="my-bucket")

    def test_connect_passes_credentials(self):
        conn = _s3_conn(access_key="AK123", secret_key="SK456", region="eu-west-1")
        mock_s3 = MagicMock()
        with patch("boto3.client", return_value=mock_s3) as mock_factory:
            client = S3Client()
            client.connect(conn)
        call_kwargs = mock_factory.call_args[1]
        assert call_kwargs["aws_access_key_id"] == "AK123"
        assert call_kwargs["aws_secret_access_key"] == "SK456"
        assert call_kwargs["region_name"] == "eu-west-1"

    def test_connect_passes_endpoint_url(self):
        conn = _s3_conn(endpoint_url="https://minio.example.com")
        mock_s3 = MagicMock()
        with patch("boto3.client", return_value=mock_s3) as mock_factory:
            client = S3Client()
            client.connect(conn)
        call_kwargs = mock_factory.call_args[1]
        assert call_kwargs["endpoint_url"] == "https://minio.example.com"

    def test_connect_no_endpoint_when_empty(self):
        conn = _s3_conn(endpoint_url="")
        mock_s3 = MagicMock()
        with patch("boto3.client", return_value=mock_s3) as mock_factory:
            client = S3Client()
            client.connect(conn)
        call_kwargs = mock_factory.call_args[1]
        assert "endpoint_url" not in call_kwargs

    def test_connect_raises_auth_error_on_403(self):
        from botocore.exceptions import ClientError
        conn = _s3_conn()
        mock_s3 = MagicMock()
        mock_s3.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket"
        )
        with patch("boto3.client", return_value=mock_s3):
            client = S3Client()
            with pytest.raises(CloudAuthError):
                client.connect(conn)

    def test_connect_raises_connection_error_on_404(self):
        from botocore.exceptions import ClientError
        conn = _s3_conn()
        mock_s3 = MagicMock()
        mock_s3.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )
        with patch("boto3.client", return_value=mock_s3):
            client = S3Client()
            with pytest.raises(CloudConnectionError, match="does not exist"):
                client.connect(conn)

    def test_connect_raises_no_credentials_error(self):
        from botocore.exceptions import NoCredentialsError
        conn = _s3_conn(access_key="", secret_key="")
        mock_s3 = MagicMock()
        mock_s3.head_bucket.side_effect = NoCredentialsError()
        with patch("boto3.client", return_value=mock_s3):
            client = S3Client()
            with pytest.raises(CloudAuthError, match="credentials"):
                client.connect(conn)

    def test_is_connected_after_connect(self):
        client, _ = _make_s3_client()
        assert client.is_connected() is True

    def test_is_connected_before_connect(self):
        client = S3Client()
        assert client.is_connected() is False

    def test_close_resets_state(self):
        client, _ = _make_s3_client()
        client.close()
        assert client.is_connected() is False

    def test_context_manager(self):
        conn = _s3_conn()
        mock_s3 = MagicMock()
        with patch("boto3.client", return_value=mock_s3):
            with S3Client() as client:
                client.connect(conn)
                assert client.is_connected()
        assert not client.is_connected()

    def test_connect_raises_if_wrong_protocol(self):
        conn = Connection(name="SFTP", host="h", user="u", protocol="sftp")
        with patch("boto3.client"):
            client = S3Client()
            with pytest.raises(ValueError, match="S3 cloud config"):
                client.connect(conn)


# ════════════════════════════════════════════════════════════════════════════
# S3Client — listdir()
# ════════════════════════════════════════════════════════════════════════════

class TestS3ClientListdir:
    def _make_page(self, prefixes=None, contents=None) -> dict:
        page: dict = {}
        if prefixes:
            page["CommonPrefixes"] = [{"Prefix": p} for p in prefixes]
        if contents:
            from datetime import datetime, timezone
            page["Contents"] = [
                {
                    "Key": k,
                    "Size": s,
                    "LastModified": datetime(2024, 1, 1, tzinfo=timezone.utc),
                }
                for k, s in contents
            ]
        return page

    def test_listdir_root_returns_entries(self):
        client, mock_s3 = _make_s3_client()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            self._make_page(
                prefixes=["images/", "docs/"],
                contents=[("readme.txt", 1024)],
            )
        ]
        entries = client.listdir("/")
        names = [e.name for e in entries]
        assert "images" in names
        assert "docs" in names
        assert "readme.txt" in names

    def test_listdir_directories_first(self):
        client, mock_s3 = _make_s3_client()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            self._make_page(
                prefixes=["zfolder/"],
                contents=[("afile.txt", 100)],
            )
        ]
        entries = client.listdir("/")
        assert entries[0].is_dir is True
        assert entries[0].name == "zfolder"

    def test_listdir_directory_entries_have_is_dir_true(self):
        client, mock_s3 = _make_s3_client()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            self._make_page(prefixes=["sub/"])
        ]
        entries = client.listdir("/")
        assert entries[0].is_dir is True

    def test_listdir_file_entries_have_correct_size(self):
        client, mock_s3 = _make_s3_client()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            self._make_page(contents=[("bigfile.bin", 9999)])
        ]
        entries = client.listdir("/")
        assert entries[0].size == 9999

    def test_listdir_skips_placeholder_key(self):
        """The directory placeholder key (same as prefix) must not appear."""
        client, mock_s3 = _make_s3_client(_s3_conn(prefix="data/"))
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            self._make_page(
                # The 'data/' key is the folder placeholder — should be filtered
                contents=[("data/", 0), ("data/file.txt", 100)],
            )
        ]
        entries = client.listdir("/")
        names = [e.name for e in entries]
        assert "" not in names
        assert "file.txt" in names

    def test_listdir_raises_when_not_connected(self):
        client = S3Client()
        with pytest.raises(CloudConnectionError):
            client.listdir("/")

    def test_listdir_passes_correct_prefix_to_paginator(self):
        client, mock_s3 = _make_s3_client(_s3_conn(prefix="backups/"))
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{}]
        client.listdir("2024/")
        mock_paginator.paginate.assert_called_once_with(
            Bucket="my-bucket", Prefix="backups/2024/", Delimiter="/"
        )


# ════════════════════════════════════════════════════════════════════════════
# S3Client — upload / download
# ════════════════════════════════════════════════════════════════════════════

class TestS3ClientUploadDownload:
    def test_upload_calls_upload_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        client, mock_s3 = _make_s3_client()
        client.upload(str(f), "/remote/test.txt")
        mock_s3.upload_file.assert_called_once_with(
            Filename=str(f),
            Bucket="my-bucket",
            Key="remote/test.txt",
        )

    def test_upload_with_prefix(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("x")
        client, mock_s3 = _make_s3_client(_s3_conn(prefix="data/"))
        client.upload(str(f), "/test.txt")
        mock_s3.upload_file.assert_called_once_with(
            Filename=str(f),
            Bucket="my-bucket",
            Key="data/test.txt",
        )

    def test_upload_with_progress_callback(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"x" * 100)
        client, mock_s3 = _make_s3_client()

        calls = []
        client.upload(str(f), "/test.txt", progress_cb=lambda t, tot: calls.append((t, tot)))
        # Check upload_file was called with a Callback kwarg
        call_kwargs = mock_s3.upload_file.call_args[1]
        assert "Callback" in call_kwargs

    def test_download_calls_download_file(self, tmp_path):
        client, mock_s3 = _make_s3_client()
        mock_s3.head_object.return_value = {"ContentLength": 500}
        dest = str(tmp_path / "out.bin")
        client.download("/remote/file.bin", dest)
        mock_s3.download_file.assert_called_once_with(
            Bucket="my-bucket",
            Key="remote/file.bin",
            Filename=dest,
        )

    def test_download_with_progress_callback(self, tmp_path):
        client, mock_s3 = _make_s3_client()
        mock_s3.head_object.return_value = {"ContentLength": 1000}
        dest = str(tmp_path / "out.bin")
        client.download("/remote/file.bin", dest, progress_cb=lambda t, tot: None)
        call_kwargs = mock_s3.download_file.call_args[1]
        assert "Callback" in call_kwargs


# ════════════════════════════════════════════════════════════════════════════
# S3Client — delete / mkdir / rename / object_size
# ════════════════════════════════════════════════════════════════════════════

class TestS3ClientOps:
    def test_delete_calls_delete_object(self):
        client, mock_s3 = _make_s3_client()
        client.delete("/path/to/file.txt")
        mock_s3.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="path/to/file.txt"
        )

    def test_mkdir_puts_slash_key(self):
        client, mock_s3 = _make_s3_client()
        client.mkdir("/newfolder")
        mock_s3.put_object.assert_called_once_with(
            Bucket="my-bucket", Key="newfolder/", Body=b""
        )

    def test_mkdir_key_always_ends_with_slash(self):
        client, mock_s3 = _make_s3_client()
        client.mkdir("/already/slash/")
        mock_s3.put_object.assert_called_once_with(
            Bucket="my-bucket", Key="already/slash/", Body=b""
        )

    def test_rename_copies_then_deletes(self):
        client, mock_s3 = _make_s3_client()
        client.rename("/old.txt", "/new.txt")
        mock_s3.copy_object.assert_called_once_with(
            CopySource={"Bucket": "my-bucket", "Key": "old.txt"},
            Bucket="my-bucket",
            Key="new.txt",
        )
        mock_s3.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="old.txt"
        )

    def test_rename_raises_cloud_operation_error_on_failure(self):
        client, mock_s3 = _make_s3_client()
        mock_s3.copy_object.side_effect = Exception("network error")
        with pytest.raises(CloudOperationError):
            client.rename("/a.txt", "/b.txt")

    def test_object_size_returns_size(self):
        client, mock_s3 = _make_s3_client()
        mock_s3.head_object.return_value = {"ContentLength": 42}
        assert client.object_size("/file.txt") == 42

    def test_object_size_returns_zero_on_error(self):
        client, mock_s3 = _make_s3_client()
        mock_s3.head_object.side_effect = Exception("not found")
        assert client.object_size("/missing.txt") == 0

    def test_delete_recursive(self):
        client, mock_s3 = _make_s3_client()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {"Contents": [{"Key": "dir/a.txt"}, {"Key": "dir/b.txt"}]}
        ]
        client.delete_recursive("/dir")
        mock_s3.delete_objects.assert_called_once()
        delete_call = mock_s3.delete_objects.call_args[1]
        keys = [o["Key"] for o in delete_call["Delete"]["Objects"]]
        assert set(keys) == {"dir/a.txt", "dir/b.txt"}


# ════════════════════════════════════════════════════════════════════════════
# S3Client — prefix normalisation
# ════════════════════════════════════════════════════════════════════════════

class TestS3ClientPrefixNormalisation:
    def test_prefix_with_trailing_slash(self):
        assert S3Client._normalise_prefix("data/") == "data/"

    def test_prefix_without_trailing_slash(self):
        assert S3Client._normalise_prefix("data") == "data/"

    def test_prefix_with_leading_slash(self):
        assert S3Client._normalise_prefix("/data/") == "data/"

    def test_empty_prefix(self):
        assert S3Client._normalise_prefix("") == ""

    def test_full_key_no_prefix(self):
        client = S3Client()
        client._prefix = ""
        assert client._full_key("/some/path") == "some/path"
        assert client._full_key("some/path") == "some/path"

    def test_full_key_with_prefix(self):
        client = S3Client()
        client._prefix = "backups/"
        assert client._full_key("/2024/file.tar") == "backups/2024/file.tar"

    def test_ui_path_strips_prefix(self):
        client = S3Client()
        client._prefix = "data/"
        assert client._ui_path("data/file.txt") == "/file.txt"

    def test_ui_path_no_prefix(self):
        client = S3Client()
        client._prefix = ""
        assert client._ui_path("file.txt") == "/file.txt"


# ════════════════════════════════════════════════════════════════════════════
# GCSClient — stub behaviour
# ════════════════════════════════════════════════════════════════════════════

class TestGCSClientStub:
    def _gcs_conn(self) -> Connection:
        cloud = CloudConfig(provider="gcs", bucket="my-gcs-bucket")
        return Connection(name="GCS", protocol="gcs", cloud=cloud)

    def test_connect_raises_not_installed(self):
        client = GCSClient()
        conn = self._gcs_conn()
        with pytest.raises(CloudProviderNotInstalled, match="google-cloud-storage"):
            client.connect(conn)

    def test_is_connected_always_false(self):
        assert GCSClient().is_connected() is False

    def test_listdir_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().listdir("/")

    def test_upload_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().upload("/local", "/remote")

    def test_download_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().download("/remote", "/local")

    def test_delete_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().delete("/file")

    def test_mkdir_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().mkdir("/dir")

    def test_rename_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().rename("/a", "/b")

    def test_object_size_raises_not_installed(self):
        with pytest.raises(CloudProviderNotInstalled):
            GCSClient().object_size("/file")

    def test_close_does_not_raise(self):
        GCSClient().close()  # should be a no-op
