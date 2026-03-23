"""
Cloud storage client abstraction — S3 and GCS backends.

Design
------
``CloudClient`` is an abstract base that mirrors the filesystem-like API
exposed by ``SFTPClient`` so the rest of the UI can work with both protocols
through a common interface.

``S3Client``  — wraps boto3; supports AWS S3 and any S3-compatible API
               (MinIO, Backblaze B2, DigitalOcean Spaces, Wasabi …).

``GCSClient`` — stub; full implementation requires google-cloud-storage.

Both clients interpret *paths* as keys relative to the configured bucket
prefix.  A leading ``/`` is stripped for consistency with how users type paths
in the UI.

boto3 / google-cloud-storage are optional runtime dependencies; import errors
produce a clear ``CloudProviderNotInstalled`` exception rather than a raw
``ModuleNotFoundError``.
"""
from __future__ import annotations

import io
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Optional

from sftp_ui.core.connection import CloudConfig, Connection
from sftp_ui.core.sftp_client import RemoteEntry  # reuse the same DTO


# ── exceptions ────────────────────────────────────────────────────────────────

class CloudProviderNotInstalled(Exception):
    """Raised when the required third-party package (boto3, etc.) is missing."""


class CloudAuthError(Exception):
    """Raised when the provided credentials are invalid or insufficient."""


class CloudConnectionError(Exception):
    """Raised when we cannot reach the storage endpoint."""


class CloudOperationError(Exception):
    """Raised for failed storage operations (delete, copy, etc.)."""


# ── abstract base ─────────────────────────────────────────────────────────────

class CloudClient(ABC):
    """Abstract filesystem-like interface for cloud storage backends."""

    @abstractmethod
    def connect(self, conn: Connection) -> None:
        """Initialise the client and validate credentials."""

    @abstractmethod
    def close(self) -> None:
        """Release any held resources / sessions."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the client is ready to accept operations."""

    @abstractmethod
    def listdir(self, path: str) -> list[RemoteEntry]:
        """List the contents of *path* (a virtual directory / prefix)."""

    @abstractmethod
    def upload(
        self,
        local_path: str,
        remote_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Upload *local_path* to *remote_path*."""

    @abstractmethod
    def download(
        self,
        remote_path: str,
        local_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Download *remote_path* to *local_path*."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a single file / object at *path*."""

    @abstractmethod
    def mkdir(self, path: str) -> None:
        """Create a virtual directory (zero-byte key ending in ``/``)."""

    @abstractmethod
    def rename(self, src: str, dst: str) -> None:
        """Rename / move an object from *src* to *dst*."""

    @abstractmethod
    def object_size(self, path: str) -> int:
        """Return the size in bytes of *path*, or 0 if it does not exist."""

    # ── context-manager support ───────────────────────────────────────────────

    def __enter__(self) -> "CloudClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()


# ── S3 client ─────────────────────────────────────────────────────────────────

class S3Client(CloudClient):
    """
    Amazon S3 / S3-compatible storage client built on top of **boto3**.

    Virtual directories
    ~~~~~~~~~~~~~~~~~~~
    S3 has no real directory concept; "folders" are represented by common key
    prefixes.  ``listdir()`` uses the ``Delimiter='/'`` trick so the UI sees
    folder entries alongside object entries — identical to how the AWS console
    presents buckets.

    Prefix handling
    ~~~~~~~~~~~~~~~
    ``conn.cloud.prefix`` is prepended to every key so the UI is rooted at an
    arbitrary sub-path within the bucket.  Trailing ``/`` is normalised
    automatically.
    """

    # Chunk size for multipart-style progress callbacks (not boto3 multipart;
    # we rely on boto3's own upload_file / download_file for chunking).
    _PROGRESS_INTERVAL = 1024 * 1024  # 1 MB

    def __init__(self) -> None:
        self._s3 = None            # boto3.client("s3")
        self._bucket: str = ""
        self._prefix: str = ""     # normalised, always ends with "/" or is ""
        self._conn: Optional[Connection] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self, conn: Connection) -> None:
        """Create a boto3 S3 client and validate credentials with a head-bucket call."""
        try:
            import boto3
            from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
        except ImportError as exc:
            raise CloudProviderNotInstalled(
                "boto3 is required for S3 connections. "
                "Install it with: pip install boto3"
            ) from exc

        cloud = conn.cloud
        if cloud is None or cloud.provider != "s3":
            raise ValueError("Connection does not have an S3 cloud config")

        # Build kwargs for boto3.client; endpoint_url is optional (AWS default)
        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": cloud.region or None,
        }
        if cloud.access_key and cloud.secret_key:
            client_kwargs["aws_access_key_id"] = cloud.access_key
            client_kwargs["aws_secret_access_key"] = cloud.secret_key
        if cloud.endpoint_url:
            client_kwargs["endpoint_url"] = cloud.endpoint_url

        s3 = boto3.client(**client_kwargs)

        # Validate credentials + bucket existence
        try:
            s3.head_bucket(Bucket=cloud.bucket)
        except NoCredentialsError as exc:
            raise CloudAuthError(
                "No valid credentials found. Check access key and secret key."
            ) from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("403", "AccessDenied"):
                raise CloudAuthError(
                    f"Access denied to bucket '{cloud.bucket}'. "
                    "Check your credentials and bucket permissions."
                ) from exc
            if code in ("404", "NoSuchBucket"):
                raise CloudConnectionError(
                    f"Bucket '{cloud.bucket}' does not exist."
                ) from exc
            raise CloudConnectionError(str(exc)) from exc
        except EndpointConnectionError as exc:
            raise CloudConnectionError(
                f"Cannot reach S3 endpoint: {exc}"
            ) from exc

        self._s3 = s3
        self._bucket = cloud.bucket
        self._prefix = self._normalise_prefix(cloud.prefix)
        self._conn = conn

    def close(self) -> None:
        self._s3 = None
        self._bucket = ""
        self._prefix = ""
        self._conn = None

    def is_connected(self) -> bool:
        return self._s3 is not None

    # ── directory listing ─────────────────────────────────────────────────────

    def listdir(self, path: str) -> list[RemoteEntry]:
        """List a virtual directory inside the bucket.

        *path* is relative to the bucket prefix.  For the root pass ``""`` or
        ``"/"``.  The call uses ``list_objects_v2`` with ``Delimiter='/'`` so
        "folder" prefixes are surfaced as directory entries.
        """
        self._require_connection()
        prefix = self._full_key(path)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        # Root of bucket → empty string
        if prefix == "/":
            prefix = ""

        paginator = self._s3.get_paginator("list_objects_v2")
        entries: list[RemoteEntry] = []

        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix, Delimiter="/"):
            # CommonPrefixes → virtual directories
            for cp in page.get("CommonPrefixes") or []:
                key = cp["Prefix"]
                name = key.rstrip("/").rsplit("/", 1)[-1]
                # Derive the UI path: strip the bucket prefix, keep the rest
                ui_path = self._ui_path(key.rstrip("/"))
                entries.append(RemoteEntry(
                    name=name,
                    path=ui_path,
                    is_dir=True,
                    size=0,
                    mtime=0,
                ))
            # Contents → objects (skip the prefix key itself)
            for obj in page.get("Contents") or []:
                key: str = obj["Key"]
                if key == prefix:
                    continue  # skip the folder placeholder key
                name = key.rsplit("/", 1)[-1]
                ui_path = self._ui_path(key)
                last_mod = obj.get("LastModified")
                mtime = int(last_mod.timestamp()) if last_mod else 0
                entries.append(RemoteEntry(
                    name=name,
                    path=ui_path,
                    is_dir=False,
                    size=obj.get("Size", 0),
                    mtime=mtime,
                ))

        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    # ── upload ────────────────────────────────────────────────────────────────

    def upload(
        self,
        local_path: str,
        remote_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Upload a local file to *remote_path* (relative to the bucket prefix).

        *progress_cb* receives ``(bytes_transferred, total_bytes)`` at regular
        intervals; it is optional.
        """
        self._require_connection()
        key = self._full_key(remote_path)

        if progress_cb is not None:
            import os
            total = os.path.getsize(local_path)
            transferred = 0

            def _callback(chunk: int) -> None:
                nonlocal transferred
                transferred += chunk
                progress_cb(transferred, total)

            self._s3.upload_file(
                Filename=local_path,
                Bucket=self._bucket,
                Key=key,
                Callback=_callback,
            )
        else:
            self._s3.upload_file(Filename=local_path, Bucket=self._bucket, Key=key)

    # ── download ──────────────────────────────────────────────────────────────

    def download(
        self,
        remote_path: str,
        local_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Download *remote_path* from the bucket to *local_path*.

        *progress_cb* receives ``(bytes_transferred, total_bytes)`` during the
        transfer; it is optional.
        """
        self._require_connection()
        key = self._full_key(remote_path)

        if progress_cb is not None:
            # Fetch object size for the total
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
            total = head.get("ContentLength", 0)
            transferred = 0

            def _callback(chunk: int) -> None:
                nonlocal transferred
                transferred += chunk
                progress_cb(transferred, total)

            self._s3.download_file(
                Bucket=self._bucket,
                Key=key,
                Filename=local_path,
                Callback=_callback,
            )
        else:
            self._s3.download_file(Bucket=self._bucket, Key=key, Filename=local_path)

    # ── delete ────────────────────────────────────────────────────────────────

    def delete(self, path: str) -> None:
        """Delete a single object."""
        self._require_connection()
        key = self._full_key(path)
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise CloudOperationError(f"delete failed for {path!r}: {exc}") from exc

    def delete_recursive(self, path: str) -> None:
        """Delete all objects whose keys start with *path* (simulates rmdir -rf)."""
        self._require_connection()
        prefix = self._full_key(path)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in (page.get("Contents") or [])]
            if objects:
                self._s3.delete_objects(
                    Bucket=self._bucket, Delete={"Objects": objects}
                )

    # ── mkdir ─────────────────────────────────────────────────────────────────

    def mkdir(self, path: str) -> None:
        """Create a virtual directory by uploading a zero-byte placeholder key."""
        self._require_connection()
        key = self._full_key(path)
        if not key.endswith("/"):
            key += "/"
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=b"")

    # ── rename ────────────────────────────────────────────────────────────────

    def rename(self, src: str, dst: str) -> None:
        """Copy *src* to *dst* then delete *src* (S3 has no native rename)."""
        self._require_connection()
        src_key = self._full_key(src)
        dst_key = self._full_key(dst)
        copy_src = {"Bucket": self._bucket, "Key": src_key}
        try:
            self._s3.copy_object(
                CopySource=copy_src,
                Bucket=self._bucket,
                Key=dst_key,
            )
            self._s3.delete_object(Bucket=self._bucket, Key=src_key)
        except Exception as exc:
            raise CloudOperationError(
                f"rename {src!r} → {dst!r} failed: {exc}"
            ) from exc

    # ── size ──────────────────────────────────────────────────────────────────

    def object_size(self, path: str) -> int:
        """Return size of *path* in bytes, or 0 if it does not exist."""
        self._require_connection()
        key = self._full_key(path)
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
            return head.get("ContentLength", 0)
        except Exception:
            return 0

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _normalise_prefix(prefix: str) -> str:
        """Strip leading slash; ensure trailing slash if non-empty."""
        p = prefix.strip("/")
        return p + "/" if p else ""

    def _full_key(self, path: str) -> str:
        """Combine bucket prefix with a UI-relative path into a full S3 key."""
        # Strip leading slash from the UI path
        rel = path.lstrip("/")
        if self._prefix:
            return self._prefix + rel
        return rel

    def _ui_path(self, key: str) -> str:
        """Convert a raw S3 key back to a UI-relative path (strip bucket prefix)."""
        if self._prefix and key.startswith(self._prefix):
            return "/" + key[len(self._prefix):]
        return "/" + key

    def _require_connection(self) -> None:
        if self._s3 is None:
            raise CloudConnectionError("Not connected — call connect() first")


# ── GCS client stub ───────────────────────────────────────────────────────────

class GCSClient(CloudClient):
    """
    Google Cloud Storage client (stub — full implementation requires
    ``google-cloud-storage``).

    This skeleton is present so the UI can reference the class without
    crashing; actual operations raise ``CloudProviderNotInstalled`` until
    ``google-cloud-storage`` is installed.
    """

    _NOT_IMPL_MSG = (
        "google-cloud-storage is required for GCS connections. "
        "Install it with: pip install google-cloud-storage"
    )

    def connect(self, conn: Connection) -> None:
        try:
            import google.cloud.storage  # noqa: F401
        except ImportError as exc:
            raise CloudProviderNotInstalled(self._NOT_IMPL_MSG) from exc
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def close(self) -> None:
        pass

    def is_connected(self) -> bool:
        return False

    def listdir(self, path: str) -> list[RemoteEntry]:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def upload(self, local_path: str, remote_path: str, progress_cb=None) -> None:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def download(self, remote_path: str, local_path: str, progress_cb=None) -> None:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def delete(self, path: str) -> None:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def mkdir(self, path: str) -> None:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def rename(self, src: str, dst: str) -> None:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)

    def object_size(self, path: str) -> int:
        raise CloudProviderNotInstalled(self._NOT_IMPL_MSG)


# ── factory ───────────────────────────────────────────────────────────────────

def make_cloud_client(conn: Connection) -> CloudClient:
    """Return the appropriate ``CloudClient`` subclass for *conn*.

    Raises ``ValueError`` if the connection protocol is not a cloud protocol.
    """
    if conn.protocol == "s3":
        return S3Client()
    if conn.protocol == "gcs":
        return GCSClient()
    raise ValueError(
        f"make_cloud_client() called with non-cloud protocol {conn.protocol!r}"
    )
