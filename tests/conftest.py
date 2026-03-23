"""
Shared fixtures for all test modules.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sftp_ui.core.connection import Connection, ConnectionStore
from sftp_ui.core.transfer import TransferJob

# Force Qt offscreen rendering before any other native library (paramiko/cffi)
# can load and potentially conflict with Qt's memory management.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ── Eagerly initialise Qt at collection time ──────────────────────────────────
# conftest.py is imported before any test module, so this ensures QApplication
# exists before paramiko / cffi are imported (which happens during collection
# of test_sftp_client_integration.py).  Without this, the native-library load
# order can corrupt Qt's internal allocator, causing Bus/Segfault errors.
from PySide6.QtWidgets import QApplication as _QApplication
_QAPP = _QApplication.instance() or _QApplication(sys.argv)

# Pre-import paramiko after Qt is initialised so cffi's allocator hooks
# are registered in the correct order relative to Qt's allocator.
try:
    import paramiko as _paramiko  # noqa: F401
except ImportError:
    pass


# ── Shared QApplication fixture (session-scoped) ──────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return _QApplication.instance() or _QApplication(sys.argv)


# ── connection fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_key(tmp_path):
    """A fake but valid-looking absolute path to an SSH key file."""
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"fake key content")
    return str(key)


@pytest.fixture
def basic_conn(tmp_key):
    return Connection(
        name="test-server",
        host="192.168.1.1",
        user="admin",
        port=22,
        key_path=tmp_key,
    )


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(path=tmp_path / "connections.json")


# ── SFTP client mock ─────────────────────────────────────────────────────────

class FakeSFTPFile:
    """Mimics paramiko.SFTPFile backed by a BytesIO buffer."""

    def __init__(self, buf: io.BytesIO, mode: str = "rb"):
        self._buf = buf
        self._mode = mode
        if "a" in mode:
            self._buf.seek(0, io.SEEK_END)
        elif "w" in mode:
            self._buf.seek(0)
            self._buf.truncate()

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def set_pipelined(self, flag: bool) -> None:
        pass

    def prefetch(self, file_size: int = 0) -> None:
        pass

    def seek(self, offset: int) -> None:
        self._buf.seek(offset)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakeSFTPClient:
    """
    In-memory SFTP client for testing.
    remote_files: dict[remote_path -> bytearray] of already-present bytes.
    """

    def __init__(self, remote_files: dict[str, bytearray] | None = None):
        self._files: dict[str, bytearray] = {
            k: bytearray(v) for k, v in (remote_files or {}).items()
        }

    def remote_size(self, remote_path: str) -> int:
        return len(self._files.get(remote_path, b""))

    def open_remote(self, remote_path: str, mode: str = "rb") -> FakeSFTPFile:
        if "w" in mode:
            self._files[remote_path] = bytearray()
        elif "a" in mode:
            if remote_path not in self._files:
                self._files[remote_path] = bytearray()
        buf = io.BytesIO(self._files[remote_path])
        if "a" in mode:
            buf.seek(0, io.SEEK_END)

        # Patch write so it actually extends our bytearray
        file_ref = self._files[remote_path]

        class _ProxyFile(FakeSFTPFile):
            def write(self_, data: bytes) -> int:
                file_ref.extend(data)
                return len(data)

        return _ProxyFile(buf, mode)

    def stat(self, remote_path: str):
        if remote_path not in self._files:
            raise FileNotFoundError(f"No such file: {remote_path!r}")
        class _Attr:
            pass
        a = _Attr()
        a.st_size = len(self._files[remote_path])
        return a

    def chmod(self, remote_path: str, mode: int) -> None:
        """Record the last chmod call for assertion in tests."""
        self._chmod_calls: dict[str, int]
        if not hasattr(self, "_chmod_calls"):
            self._chmod_calls = {}
        self._chmod_calls[remote_path] = mode

    def get_content(self, remote_path: str) -> bytes:
        return bytes(self._files.get(remote_path, b""))

    def get_chmod(self, remote_path: str) -> int | None:
        calls = getattr(self, "_chmod_calls", {})
        return calls.get(remote_path)


@pytest.fixture
def fake_sftp():
    return FakeSFTPClient()


# ── local file fixture ───────────────────────────────────────────────────────

@pytest.fixture
def local_file(tmp_path):
    """Returns (path_str, content_bytes) for a 1 MB temp file."""
    content = os.urandom(1024 * 1024)  # 1 MB
    p = tmp_path / "upload_me.bin"
    p.write_bytes(content)
    return str(p), content


@pytest.fixture
def make_local_file(tmp_path):
    """Factory: make_local_file(size_bytes) → (path_str, content_bytes)."""
    def _make(size: int = 1024, name: str = "file.bin") -> tuple[str, bytes]:
        content = os.urandom(size)
        p = tmp_path / name
        p.write_bytes(content)
        return str(p), content
    return _make


@pytest.fixture
def make_job(tmp_path):
    """Factory: make_job(local_path, remote_path) → TransferJob."""
    def _make(local_path: str, remote_path: str = "/remote/file.bin") -> TransferJob:
        return TransferJob(local_path=local_path, remote_path=remote_path)
    return _make
