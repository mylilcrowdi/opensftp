"""
Integration tests for SFTPClient against an in-process paramiko SFTP server.

The server runs in a background thread on a random loopback port.
No Docker, no external deps — uses paramiko's own server infrastructure.
"""
from __future__ import annotations

import io
import os
import socket
import stat
import threading
from pathlib import PurePosixPath

import paramiko
import pytest

from sftp_ui.core.connection import Connection
from sftp_ui.core.sftp_client import SFTPClient


# ── In-memory filesystem ─────────────────────────────────────────────────────

class _InMemFS:
    """Minimal in-memory filesystem for SFTP tests."""

    def __init__(self) -> None:
        self.dirs: set[str] = {"/"}
        self.files: dict[str, bytes] = {}
        self.symlinks: set[str] = set()   # paths treated as symlinks

    def add_file(self, path: str, content: bytes = b"") -> None:
        self.files[path] = content

    def add_dir(self, path: str) -> None:
        self.dirs.add(path)

    def add_symlink(self, path: str) -> None:
        """Register path as a symlink (content is empty)."""
        self.symlinks.add(path)
        self.files[path] = b""


# ── paramiko SFTP handle ──────────────────────────────────────────────────────

class _SFTPHandle(paramiko.SFTPHandle):
    def __init__(self, fs: _InMemFS, path: str, flags: int) -> None:
        super().__init__(flags)
        self._fs = fs
        self._path = path
        if flags & (os.O_WRONLY | os.O_RDWR) or not (flags & os.O_RDONLY):
            if flags & os.O_TRUNC or path not in fs.files:
                fs.files[path] = b""
        self._buf = io.BytesIO(fs.files.get(path, b""))
        if flags & os.O_APPEND:
            self._buf.seek(0, io.SEEK_END)

    def read(self, offset: int, length: int) -> bytes:
        self._buf.seek(offset)
        return self._buf.read(length)

    def write(self, offset: int, data: bytes) -> int:
        self._buf.seek(offset)
        self._buf.write(data)
        self._fs.files[self._path] = self._buf.getvalue()
        return paramiko.SFTP_OK

    def stat(self) -> paramiko.SFTPAttributes:
        a = paramiko.SFTPAttributes()
        a.st_size = len(self._fs.files.get(self._path, b""))
        a.st_mtime = 1_000_000
        a.st_mode = stat.S_IFREG | 0o644
        return a

    def close(self) -> int:
        self._fs.files[self._path] = self._buf.getvalue()
        return paramiko.SFTP_OK


# ── paramiko SFTP server interface ────────────────────────────────────────────

class _SFTPServer(paramiko.SFTPServerInterface):
    """Serves _InMemFS over SFTP."""

    def __init__(self, server: "_SSHServer", *args, **kwargs) -> None:
        super().__init__(server, *args, **kwargs)
        self._fs: _InMemFS = server.fs

    def _attr_for_dir(self, path: str) -> paramiko.SFTPAttributes:
        a = paramiko.SFTPAttributes()
        a.filename = PurePosixPath(path).name or "/"
        a.st_mode = stat.S_IFDIR | 0o755
        a.st_size = 0
        a.st_mtime = 0
        return a

    def _attr_for_file(self, path: str) -> paramiko.SFTPAttributes:
        a = paramiko.SFTPAttributes()
        a.filename = PurePosixPath(path).name
        if path in self._fs.symlinks:
            a.st_mode = stat.S_IFLNK | 0o777
        else:
            a.st_mode = stat.S_IFREG | 0o644
        a.st_size = len(self._fs.files.get(path, b""))
        a.st_mtime = 1_000_000
        return a

    def list_folder(self, path: str):
        if path not in self._fs.dirs:
            return paramiko.SFTP_NO_SUCH_FILE
        parent = PurePosixPath(path)
        result = []
        for d in self._fs.dirs:
            if d == path:
                continue
            if PurePosixPath(d).parent == parent:
                result.append(self._attr_for_dir(d))
        for f in self._fs.files:
            if str(PurePosixPath(f).parent) == path:
                result.append(self._attr_for_file(f))
        return result

    def lstat(self, path: str):
        if path in self._fs.symlinks:
            return self._attr_for_file(path)
        if path in self._fs.dirs:
            return self._attr_for_dir(path)
        if path in self._fs.files:
            return self._attr_for_file(path)
        return paramiko.SFTP_NO_SUCH_FILE

    def stat(self, path: str):
        return self.lstat(path)

    def open(self, path: str, flags: int, attr):
        # Reject read-only opens on non-existent files (mirrors real SFTP behaviour)
        read_only = (flags & 3) == os.O_RDONLY
        if read_only and path not in self._fs.files:
            return paramiko.SFTP_NO_SUCH_FILE
        return _SFTPHandle(self._fs, path, flags)

    def mkdir(self, path: str, attr) -> int:
        self._fs.dirs.add(path)
        return paramiko.SFTP_OK

    def rmdir(self, path: str) -> int:
        if path not in self._fs.dirs:
            return paramiko.SFTP_NO_SUCH_FILE
        self._fs.dirs.discard(path)
        return paramiko.SFTP_OK

    def remove(self, path: str) -> int:
        if path not in self._fs.files:
            return paramiko.SFTP_NO_SUCH_FILE
        del self._fs.files[path]
        self._fs.symlinks.discard(path)
        return paramiko.SFTP_OK

    def rename(self, oldpath: str, newpath: str) -> int:
        if oldpath in self._fs.files:
            self._fs.files[newpath] = self._fs.files.pop(oldpath)
            if oldpath in self._fs.symlinks:
                self._fs.symlinks.discard(oldpath)
                self._fs.symlinks.add(newpath)
        elif oldpath in self._fs.dirs:
            self._fs.dirs.discard(oldpath)
            self._fs.dirs.add(newpath)
        else:
            return paramiko.SFTP_NO_SUCH_FILE
        return paramiko.SFTP_OK


# ── SSH server ────────────────────────────────────────────────────────────────

class _SSHServer(paramiko.ServerInterface):
    def __init__(self) -> None:
        self.fs = _InMemFS()

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return (paramiko.OPEN_SUCCEEDED if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)

    def check_auth_password(self, username: str, password: str) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username: str, key) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username: str) -> str:
        return "password,publickey"


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def sftp_server():
    """
    Start an in-process paramiko SFTP server on a random loopback port.

    Yields (host, port, password, fs) where fs is the _InMemFS instance
    tests can pre-populate or inspect.
    """
    host_key = paramiko.RSAKey.generate(1024)
    ssh_server = _SSHServer()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(20)
    sock.settimeout(1.0)

    stop = threading.Event()

    def _serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                break
            t = paramiko.Transport(conn)
            t.add_server_key(host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, _SFTPServer)
            t.start_server(server=ssh_server)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    yield "127.0.0.1", port, "testpassword", ssh_server.fs

    stop.set()
    sock.close()


def _conn(host: str, port: int, password: str) -> Connection:
    return Connection(name="test", host=host, port=port, user="testuser", password=password)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestConnect:
    def test_connect_with_password(self, sftp_server):
        host, port, pw, fs = sftp_server
        client = SFTPClient()
        client.connect(_conn(host, port, pw))
        assert client.is_connected()
        client.close()

    def test_close_is_idempotent(self, sftp_server):
        host, port, pw, fs = sftp_server
        client = SFTPClient()
        client.connect(_conn(host, port, pw))
        client.close()
        client.close()  # must not raise

    def test_context_manager(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            assert client.is_connected()
        assert not client.is_connected()


class TestListdir:
    def test_empty_root(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        assert entries == []

    def test_lists_files_and_dirs(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/docs")
        fs.add_file("/readme.txt", b"hello")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        names = {e.name for e in entries}
        assert "docs" in names
        assert "readme.txt" in names

    def test_dirs_sorted_before_files(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/alpha.txt", b"a")
        fs.add_dir("/beta")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        assert entries[0].is_dir
        assert not entries[1].is_dir

    def test_entry_attributes(self, sftp_server):
        host, port, pw, fs = sftp_server
        content = b"hello world"
        fs.add_file("/test.txt", content)
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        e = next(x for x in entries if x.name == "test.txt")
        assert e.size == len(content)
        assert e.path == "/test.txt"
        assert not e.is_dir
        assert not e.is_symlink

    def test_detects_symlinks(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_symlink("/link.txt")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        e = next(x for x in entries if x.name == "link.txt")
        assert e.is_symlink

    def test_nested_listdir(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/a")
        fs.add_dir("/a/b")
        fs.add_file("/a/b/deep.txt", b"deep")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/a/b")
        assert len(entries) == 1
        assert entries[0].name == "deep.txt"


class TestMkdir:
    def test_mkdir_creates_directory(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.mkdir("/newdir")
        assert "/newdir" in fs.dirs

    def test_mkdir_p_creates_nested(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.mkdir_p("/a/b/c")
        assert "/a" in fs.dirs
        assert "/a/b" in fs.dirs
        assert "/a/b/c" in fs.dirs

    def test_mkdir_p_is_idempotent(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/exists")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.mkdir_p("/exists")  # must not raise


class TestFileOps:
    def test_create_file(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.create_file("/empty.txt")
        assert "/empty.txt" in fs.files

    def test_rename_file(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/old.txt", b"data")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rename("/old.txt", "/new.txt")
        assert "/old.txt" not in fs.files
        assert "/new.txt" in fs.files
        assert fs.files["/new.txt"] == b"data"

    def test_remove_file(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/bye.txt", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.remove("/bye.txt")
        assert "/bye.txt" not in fs.files

    def test_remote_size_existing(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/sized.bin", b"12345")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            assert client.remote_size("/sized.bin") == 5

    def test_remote_size_missing_returns_zero(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            assert client.remote_size("/nope.bin") == 0

    def test_open_remote_write_and_read(self, sftp_server):
        host, port, pw, fs = sftp_server
        content = b"integration test content"
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with client.open_remote("/data.bin", "wb") as f:
                f.write(content)
            with client.open_remote("/data.bin", "rb") as f:
                result = f.read()
        assert result == content


class TestRmdir:
    def test_rmdir_empty_dir(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/empty")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rmdir("/empty")
        assert "/empty" not in fs.dirs

    def test_rmdir_recursive(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/tree")
        fs.add_dir("/tree/sub")
        fs.add_file("/tree/a.txt", b"a")
        fs.add_file("/tree/sub/b.txt", b"b")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rmdir_recursive("/tree")
        assert "/tree" not in fs.dirs
        assert "/tree/sub" not in fs.dirs
        assert "/tree/a.txt" not in fs.files
        assert "/tree/sub/b.txt" not in fs.files


class TestWalk:
    def test_walk_flat(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/flat")
        fs.add_file("/flat/a.txt", b"a")
        fs.add_file("/flat/b.txt", b"b")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/flat")
        names = {e.name for e in entries}
        assert names == {"a.txt", "b.txt"}

    def test_walk_nested(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/root")
        fs.add_dir("/root/sub")
        fs.add_file("/root/top.txt", b"t")
        fs.add_file("/root/sub/deep.txt", b"d")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/root")
        names = {e.name for e in entries}
        assert names == {"top.txt", "deep.txt"}

    def test_walk_returns_only_files(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/d")
        fs.add_dir("/d/sub")
        fs.add_file("/d/f.txt", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/d")
        assert all(not e.is_dir for e in entries)

# ── Edge cases ────────────────────────────────────────────────────────────────

class TestConnectErrors:
    def test_wrong_password_raises_authentication_error(self, tmp_path):
        """A key file that exists but isn't a valid key → AuthenticationError."""
        from sftp_ui.core.sftp_client import AuthenticationError
        bad_key = tmp_path / "bad_key"
        bad_key.write_bytes(b"this is not a valid private key")
        conn = Connection(
            name="t", host="127.0.0.1", port=22, user="u",
            key_path=str(bad_key),
        )
        client = SFTPClient()
        with pytest.raises(AuthenticationError):
            client.connect(conn)

    def test_unreachable_host_raises_connection_error(self):
        """Nothing listening on port 1 → ConnectionError."""
        from sftp_ui.core.sftp_client import ConnectionError as SFTPConnError
        conn = Connection(
            name="t", host="127.0.0.1", port=1, user="u", password="x",
        )
        client = SFTPClient()
        with pytest.raises(SFTPConnError):
            client.connect(conn)


class TestListdirEdgeCases:
    def test_nonexistent_path_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.listdir("/does/not/exist")

    def test_zero_byte_file_appears_with_size_zero(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/empty.txt", b"")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        e = next(x for x in entries if x.name == "empty.txt")
        assert e.size == 0
        assert not e.is_dir

    def test_unicode_filename(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/café.txt", b"data")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        assert any(e.name == "café.txt" for e in entries)

    def test_filename_with_spaces(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/my file.txt", b"data")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        assert any(e.name == "my file.txt" for e in entries)

    def test_filename_with_special_chars(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/report(final).csv", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.listdir("/")
        assert any(e.name == "report(final).csv" for e in entries)


class TestFileOpsEdgeCases:
    def test_remove_nonexistent_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.remove("/ghost.txt")

    def test_rename_nonexistent_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.rename("/ghost.txt", "/other.txt")

    def test_open_remote_read_nonexistent_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.open_remote("/nope.bin", "rb")

    def test_unicode_file_content_roundtrip(self, sftp_server):
        host, port, pw, fs = sftp_server
        content = "héllo wörld 🌍".encode("utf-8")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with client.open_remote("/unicode.txt", "wb") as f:
                f.write(content)
            with client.open_remote("/unicode.txt", "rb") as f:
                result = f.read()
        assert result == content

    def test_mkdir_p_single_segment(self, sftp_server):
        """mkdir_p('/') must be a no-op — root always exists."""
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.mkdir_p("/")   # must not raise


class TestRmdirEdgeCases:
    def test_rmdir_nonexistent_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.rmdir("/ghost")

    def test_rmdir_recursive_empty_dir(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/empty_tree")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rmdir_recursive("/empty_tree")
        assert "/empty_tree" not in fs.dirs


class TestWalkEdgeCases:
    def test_walk_empty_dir_returns_empty_list(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/void")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/void")
        assert entries == []

    def test_walk_preserves_full_paths(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/root")
        fs.add_dir("/root/sub")
        fs.add_file("/root/sub/file.txt", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/root")
        assert entries[0].path == "/root/sub/file.txt"


# ── stat ──────────────────────────────────────────────────────────────────────

class TestStat:
    def test_stat_file_returns_correct_size(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/data.bin", b"hello world")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            attrs = client.stat("/data.bin")
        assert attrs.st_size == 11

    def test_stat_missing_file_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.stat("/ghost.bin")

    def test_remote_size_returns_zero_for_missing(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            assert client.remote_size("/no_such_file.bin") == 0

    def test_remote_size_matches_content(self, sftp_server):
        host, port, pw, fs = sftp_server
        content = os.urandom(512)
        fs.add_file("/rnd.bin", content)
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            assert client.remote_size("/rnd.bin") == 512

    def test_stat_empty_file(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/empty.txt", b"")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            attrs = client.stat("/empty.txt")
        assert attrs.st_size == 0


# ── rename ────────────────────────────────────────────────────────────────────

class TestRename:
    def test_rename_file(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/old.txt", b"content")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rename("/old.txt", "/new.txt")
        assert "/new.txt" in fs.files
        assert "/old.txt" not in fs.files

    def test_renamed_file_content_preserved(self, sftp_server):
        host, port, pw, fs = sftp_server
        content = b"important data"
        fs.add_file("/src.bin", content)
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rename("/src.bin", "/dst.bin")
        assert fs.files["/dst.bin"] == content

    def test_rename_directory(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/old_dir")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.rename("/old_dir", "/new_dir")
        assert "/new_dir" in fs.dirs
        assert "/old_dir" not in fs.dirs

    def test_rename_nonexistent_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.rename("/ghost.txt", "/anywhere.txt")


# ── large file / binary integrity ─────────────────────────────────────────────

class TestBinaryData:
    def test_all_byte_values_round_trip(self, sftp_server, tmp_path):
        """Every byte value 0x00–0xFF must survive write + read."""
        host, port, pw, fs = sftp_server
        content = bytes(range(256)) * 64   # 16 KB
        local_in = tmp_path / "in.bin"
        local_out = tmp_path / "out.bin"
        local_in.write_bytes(content)

        from sftp_ui.core.transfer import TransferJob, TransferDirection, TransferEngine, CHUNK_SIZE
        from tests.conftest import FakeSFTPClient

        # Write via SFTP server, then read back via fs
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with client.open_remote("/bytes.bin", "wb") as f:
                f.write(content)
            with client.open_remote("/bytes.bin", "rb") as f:
                result = f.read(len(content))
        assert result == content

    def test_null_bytes_round_trip(self, sftp_server):
        host, port, pw, fs = sftp_server
        content = b"\x00" * 1024
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with client.open_remote("/nulls.bin", "wb") as f:
                f.write(content)
            with client.open_remote("/nulls.bin", "rb") as f:
                result = f.read(len(content))
        assert result == content


# ── overwrite ─────────────────────────────────────────────────────────────────

class TestOverwrite:
    def test_open_wb_truncates_existing(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/file.txt", b"original content that is long")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with client.open_remote("/file.txt", "wb") as f:
                f.write(b"new")
            with client.open_remote("/file.txt", "rb") as f:
                result = f.read()
        assert result == b"new"

    def test_open_ab_appends(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/log.txt", b"line1\n")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with client.open_remote("/log.txt", "ab") as f:
                f.write(b"line2\n")
        assert fs.files["/log.txt"] == b"line1\nline2\n"

    def test_create_file_then_overwrite(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.create_file("/new.txt")
            assert fs.files.get("/new.txt") == b""
            with client.open_remote("/new.txt", "wb") as f:
                f.write(b"data")
        assert fs.files["/new.txt"] == b"data"


# ── walk depth ────────────────────────────────────────────────────────────────

class TestWalkDepth:
    def test_walk_deeply_nested(self, sftp_server):
        host, port, pw, fs = sftp_server
        # Build /a/b/c/d/leaf.txt
        for d in ["/a", "/a/b", "/a/b/c", "/a/b/c/d"]:
            fs.add_dir(d)
        fs.add_file("/a/b/c/d/leaf.txt", b"deep")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/a")
        assert len(entries) == 1
        assert entries[0].path == "/a/b/c/d/leaf.txt"

    def test_walk_multiple_files_at_root(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/flat")
        for i in range(5):
            fs.add_file(f"/flat/f{i}.txt", f"content{i}".encode())
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/flat")
        assert len(entries) == 5

    def test_walk_returns_only_files_not_dirs(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_dir("/mixed")
        fs.add_dir("/mixed/sub")
        fs.add_file("/mixed/file.txt", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            entries = client.walk("/mixed")
        paths = [e.path for e in entries]
        assert all(not e.is_dir for e in entries)
        assert "/mixed/file.txt" in paths
        # sub directory itself must not appear
        assert "/mixed/sub" not in paths


# ── remove ────────────────────────────────────────────────────────────────────

class TestRemove:
    def test_remove_existing_file(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/todel.txt", b"bye")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.remove("/todel.txt")
        assert "/todel.txt" not in fs.files

    def test_remove_nonexistent_raises(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            with pytest.raises(Exception):
                client.remove("/ghost.txt")

    def test_remove_leaves_other_files_intact(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/keep.txt", b"keep me")
        fs.add_file("/del.txt", b"delete me")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            client.remove("/del.txt")
        assert "/keep.txt" in fs.files


class TestListdirStream:
    def test_stream_collects_all_entries(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/a.txt", b"a")
        fs.add_file("/b.txt", b"b")
        fs.add_dir("/sub")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            batches: list = []
            client.listdir_stream("/", lambda b, f: batches.append((b, f)))
        all_entries = [e for b, _ in batches for e in b]
        names = {e.name for e in all_entries}
        assert "a.txt" in names
        assert "b.txt" in names
        assert "sub" in names

    def test_stream_last_batch_is_final(self, sftp_server):
        host, port, pw, fs = sftp_server
        fs.add_file("/x.txt", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            finals: list[bool] = []
            client.listdir_stream("/", lambda b, f: finals.append(f))
        assert finals[-1] is True
        assert all(f is False for f in finals[:-1])

    def test_stream_empty_dir_calls_final(self, sftp_server):
        host, port, pw, fs = sftp_server
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            calls: list = []
            client.listdir_stream("/", lambda b, f: calls.append((b, f)))
        assert len(calls) == 1
        batch, is_final = calls[0]
        assert batch == []
        assert is_final is True

    def test_stream_batching(self, sftp_server):
        """With batch_size=2 and 5 files, expect at least 3 calls."""
        host, port, pw, fs = sftp_server
        for i in range(5):
            fs.add_file(f"/f{i}.txt", b"x")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            calls: list = []
            client.listdir_stream("/", lambda b, f: calls.append((b, f)), batch_size=2)
        assert len(calls) >= 3

    def test_stream_matches_listdir(self, sftp_server):
        """listdir_stream must return the same entries as listdir."""
        host, port, pw, fs = sftp_server
        fs.add_file("/alpha.txt", b"a")
        fs.add_dir("/beta")
        fs.add_file("/gamma.py", b"g")
        with SFTPClient() as client:
            client.connect(_conn(host, port, pw))
            stream_entries: list = []
            client.listdir_stream("/", lambda b, f: stream_entries.extend(b))
            regular_entries = client.listdir("/")
        assert {e.name for e in stream_entries} == {e.name for e in regular_entries}
        for e in stream_entries:
            match = next(r for r in regular_entries if r.name == e.name)
            assert e.is_dir == match.is_dir
            assert e.size == match.size
            assert e.path == match.path
