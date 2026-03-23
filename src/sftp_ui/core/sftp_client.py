"""
Thin, testable wrapper around paramiko.
All SFTP operations go through here — never import paramiko directly from UI.
"""
from __future__ import annotations

import socket
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Optional

import paramiko

from sftp_ui.core.connection import Connection, TunnelConfig


@dataclass
class RemoteEntry:
    name: str
    path: str           # full remote path
    is_dir: bool
    size: int
    mtime: int          # unix timestamp
    is_symlink: bool = False
    st_mode: int = 0    # raw stat mode bits (0 when unknown)


class AuthenticationError(Exception):
    pass


class ConnectionError(Exception):
    pass


class SFTPClient:
    """
    Wraps a single SSH + SFTP session.
    Use as a context manager or call close() explicitly.

    SSH Tunneling (jump host):
      If ``conn.tunnel`` is set, the client first establishes a connection to
      the jump host, then opens a ``direct-tcpip`` channel through it to reach
      the actual target.  Both the jump-host SSH session and the target SSH
      session are closed when ``close()`` is called.
    """

    def __init__(self) -> None:
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        # Kept alive so it is closed alongside the main connection
        self._tunnel_ssh: Optional[paramiko.SSHClient] = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self, conn: Connection) -> None:
        """Open SSH + SFTP session for the given Connection.

        If ``conn.tunnel`` is configured, an intermediate jump-host SSH session
        is opened first and a ``direct-tcpip`` channel is used as the socket
        for the target SSH connection.
        """
        # ── Step 1: optional jump-host tunnel ─────────────────────────────────
        sock: Optional[socket.socket] = None
        if conn.tunnel is not None:
            sock = self._open_tunnel_channel(conn.tunnel, conn.host, conn.port)

        # ── Step 2: connect to the actual target ──────────────────────────────
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = dict(
            hostname=conn.host,
            port=conn.port,
            username=conn.user,
            timeout=15,
            look_for_keys=False,
            allow_agent=conn.use_agent,
        )
        if sock is not None:
            kwargs["sock"] = sock

        try:
            if conn.key_path:
                pkey = self._load_pkey(conn.key_path, passphrase=conn.key_passphrase)
                kwargs["pkey"] = pkey
            elif conn.password:
                kwargs["password"] = conn.password
            elif not conn.use_agent:
                raise AuthenticationError(
                    "Connection has neither key_path nor password (and SSH agent is disabled)"
                )

            ssh.connect(**kwargs)
        except paramiko.AuthenticationException as exc:
            ssh.close()
            self._close_tunnel()
            raise AuthenticationError(str(exc)) from exc
        except AuthenticationError:
            ssh.close()
            self._close_tunnel()
            raise
        except Exception as exc:
            ssh.close()
            self._close_tunnel()
            raise ConnectionError(str(exc)) from exc

        self._ssh = ssh

        # ── Performance tuning ────────────────────────────────────────────────
        # Paramiko's default SSH channel window is only 64 KB, which stalls
        # constantly on anything but tiny files.  32 MB lets large blocks be
        # in flight without waiting for window-update ACKs.
        # Rekeying (default: every 1 GB / 1 billion packets) can pause a
        # transfer mid-stream; push the threshold to effectively never.
        transport = ssh.get_transport()
        transport.window_size         = 32 * 1024 * 1024   # 32 MB channel window
        transport.packetizer.REKEY_BYTES   = 2**40         # 1 TB — effectively never
        transport.packetizer.REKEY_VOLUME  = 2**40

        self._sftp = paramiko.SFTPClient.from_transport(
            transport,
            window_size=32 * 1024 * 1024,  # per-file read/write window
            max_packet_size=32768,          # SSH packet payload limit (server cap)
        )

        # Send a keepalive every 30 s so the connection survives idle periods.
        # Without this most servers (and NAT routers) drop the TCP session after
        # ~2 min of silence, causing mysterious "broken pipe" errors on the next op.
        transport.set_keepalive(30)

    # ── tunnel helpers ───────────────────────────────────────────────────────

    def _open_tunnel_channel(
        self,
        tunnel: TunnelConfig,
        dest_host: str,
        dest_port: int,
    ) -> paramiko.Channel:
        """Connect to the jump host and return a direct-tcpip channel to dest.

        The jump-host ``SSHClient`` is stored in ``self._tunnel_ssh`` so it
        stays alive for the duration of the target connection and is closed in
        ``close()`` / ``_close_tunnel()``.
        """
        jump = paramiko.SSHClient()
        jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        jkwargs: dict = dict(
            hostname=tunnel.host,
            port=tunnel.port,
            username=tunnel.user,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )

        try:
            if tunnel.key_path:
                pkey = self._load_pkey(tunnel.key_path, passphrase=tunnel.key_passphrase)
                jkwargs["pkey"] = pkey
            elif tunnel.password:
                jkwargs["password"] = tunnel.password
            else:
                raise AuthenticationError(
                    "Tunnel has neither key_path nor password"
                )

            jump.connect(**jkwargs)
        except paramiko.AuthenticationException as exc:
            jump.close()
            raise AuthenticationError(
                f"Tunnel authentication failed for {tunnel.user}@{tunnel.host}: {exc}"
            ) from exc
        except AuthenticationError:
            jump.close()
            raise
        except Exception as exc:
            jump.close()
            raise ConnectionError(
                f"Could not connect to tunnel host {tunnel.host}:{tunnel.port}: {exc}"
            ) from exc

        transport = jump.get_transport()
        channel = transport.open_channel(
            "direct-tcpip",
            (dest_host, dest_port),
            ("127.0.0.1", 0),
        )
        self._tunnel_ssh = jump
        return channel

    def _close_tunnel(self) -> None:
        """Close the jump-host session if one is open."""
        if self._tunnel_ssh:
            self._tunnel_ssh.close()
            self._tunnel_ssh = None

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._ssh:
            self._ssh.close()
            self._ssh = None
        self._close_tunnel()

    def is_connected(self) -> bool:
        return self._sftp is not None

    def __enter__(self) -> "SFTPClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── filesystem ops ───────────────────────────────────────────────────────

    def listdir(self, remote_path: str) -> list[RemoteEntry]:
        self._require_connection()
        entries = []
        for attr in self._sftp.listdir_attr(remote_path):
            full = str(PurePosixPath(remote_path) / attr.filename)
            entries.append(RemoteEntry(
                name=attr.filename,
                path=full,
                is_dir=stat.S_ISDIR(attr.st_mode),
                size=attr.st_size or 0,
                mtime=int(attr.st_mtime or 0),
                is_symlink=stat.S_ISLNK(attr.st_mode),
                st_mode=int(attr.st_mode or 0),
            ))
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def listdir_stream(
        self,
        remote_path: str,
        on_batch: "Callable[[list[RemoteEntry], bool], None]",
        batch_size: int = 200,
    ) -> None:
        """List directory entries in batches, streaming results as they arrive.

        Calls on_batch(batch, is_final) for each batch of up to batch_size
        entries.  is_final is True only on the final call (batch may be empty).

        Uses paramiko's listdir_iter() which pipelines SFTP readdir requests
        (read_aheads=20), giving significantly lower latency on high-latency
        connections vs listdir_attr() which is fully sequential.
        """
        self._require_connection()
        batch: list[RemoteEntry] = []
        for attr in self._sftp.listdir_iter(remote_path, read_aheads=20):
            full = str(PurePosixPath(remote_path) / attr.filename)
            batch.append(RemoteEntry(
                name=attr.filename,
                path=full,
                is_dir=stat.S_ISDIR(attr.st_mode),
                size=attr.st_size or 0,
                mtime=int(attr.st_mtime or 0),
                is_symlink=stat.S_ISLNK(attr.st_mode),
                st_mode=int(attr.st_mode or 0),
            ))
            if len(batch) >= batch_size:
                on_batch(batch, False)
                batch = []
        on_batch(batch, True)

    def mkdir(self, remote_path: str) -> None:
        self._require_connection()
        self._sftp.mkdir(remote_path)

    def mkdir_p(self, remote_path: str) -> None:
        """mkdir -p: create all missing intermediate directories."""
        self._require_connection()
        parts = PurePosixPath(remote_path).parts
        current = ""
        for part in parts:
            current = str(PurePosixPath(current) / part) if current else part
            if not current or current == "/":
                continue
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def rename(self, old_path: str, new_path: str) -> None:
        self._require_connection()
        self._sftp.rename(old_path, new_path)

    def remove(self, remote_path: str) -> None:
        self._require_connection()
        self._sftp.remove(remote_path)

    def stat(self, remote_path: str) -> paramiko.SFTPAttributes:
        self._require_connection()
        return self._sftp.stat(remote_path)

    def remote_size(self, remote_path: str) -> int:
        """Return size of remote file, or 0 if it does not exist."""
        try:
            return self._sftp.stat(remote_path).st_size or 0
        except FileNotFoundError:
            return 0

    def open_remote(
        self, remote_path: str, mode: str = "rb"
    ) -> paramiko.SFTPFile:
        self._require_connection()
        return self._sftp.open(remote_path, mode)

    def rmdir(self, remote_path: str) -> None:
        """Remove an empty remote directory."""
        self._require_connection()
        self._sftp.rmdir(remote_path)

    def rmdir_recursive(self, remote_path: str) -> None:
        """Remove a directory and all its contents recursively."""
        self._require_connection()
        for attr in self._sftp.listdir_attr(remote_path):
            child = str(PurePosixPath(remote_path) / attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                self.rmdir_recursive(child)
            else:
                self._sftp.remove(child)
        self._sftp.rmdir(remote_path)

    def walk(self, remote_path: str) -> list[RemoteEntry]:
        """Return all files under remote_path, recursively (depth-first)."""
        self._require_connection()
        result: list[RemoteEntry] = []
        self._walk_recursive(remote_path, result)
        return result

    def _walk_recursive(self, remote_path: str, result: list[RemoteEntry]) -> None:
        for entry in self.listdir(remote_path):
            if entry.is_dir:
                self._walk_recursive(entry.path, result)
            else:
                result.append(entry)

    def create_file(self, remote_path: str) -> None:
        """Create an empty file at remote_path."""
        self._require_connection()
        with self._sftp.open(remote_path, "w"):
            pass

    def chmod(self, remote_path: str, mode: int) -> None:
        """Set permissions on a remote file (mode is the integer permission bits)."""
        self._require_connection()
        self._sftp.chmod(remote_path, mode)

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _load_pkey(path: str, passphrase: Optional[str] = None) -> paramiko.PKey:
        key_classes = [paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey]
        if hasattr(paramiko, "DSSKey"):
            key_classes.append(paramiko.DSSKey)
        last_exc: Exception = Exception("no key classes tried")
        for cls in key_classes:
            try:
                return cls.from_private_key_file(path, password=passphrase)
            except paramiko.ssh_exception.PasswordRequiredException:
                raise AuthenticationError(
                    f"Private key {path!r} is encrypted — enter a Key Passphrase in the connection settings."
                )
            except Exception as e:
                last_exc = e
                continue
        raise AuthenticationError(f"Could not load private key {path!r}: {last_exc}")

    def _require_connection(self) -> None:
        if not self._sftp:
            raise ConnectionError("Not connected — call connect() first")
