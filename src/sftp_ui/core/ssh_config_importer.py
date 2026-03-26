"""
SSH Config Importer: parse ~/.ssh/config and convert entries to Connection objects.

Supports:
  - HostName, User, Port, IdentityFile
  - ProxyJump (single hop) mapped to TunnelConfig
  - Wildcard/glob hosts are skipped (not bookmarkable)
  - Expands ~ and environment variables in IdentityFile paths
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import paramiko

from sftp_ui.core.connection import Connection, TunnelConfig

# Pattern that marks a host entry as a wildcard/pattern (not a real bookmark)
_WILDCARD_RE = re.compile(r"[*?!]")

DEFAULT_SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"


class SSHConfigImporter:
    """Parse an OpenSSH config file and produce a list of :class:`Connection` objects.

    Parameters
    ----------
    path:
        Path to the SSH config file. Defaults to ``~/.ssh/config``.
    """

    def __init__(self, path: Path | str = DEFAULT_SSH_CONFIG_PATH) -> None:
        self._path = Path(path)

    # ── public API ────────────────────────────────────────────────────────────

    def import_connections(self) -> list[Connection]:
        """Return a list of :class:`Connection` objects parsed from the SSH config.

        Entries that cannot be converted to valid connections (e.g. wildcards,
        missing hostname) are silently skipped.
        """
        config = self._load_config()
        if config is None:
            return []

        connections: list[Connection] = []
        for alias in self._bookmarkable_hosts(config):
            conn = self._host_to_connection(alias, config)
            if conn is not None:
                connections.append(conn)
        return connections

    # ── private helpers ───────────────────────────────────────────────────────

    def _load_config(self) -> Optional[paramiko.SSHConfig]:
        if not self._path.exists():
            return None
        try:
            cfg = paramiko.SSHConfig.from_path(str(self._path))
        except Exception:
            return None
        return cfg

    def _bookmarkable_hosts(self, config: paramiko.SSHConfig) -> list[str]:
        """Return host aliases that are concrete (no wildcards)."""
        return [
            h for h in config.get_hostnames()
            if not _WILDCARD_RE.search(h)
        ]

    def _host_to_connection(
        self,
        alias: str,
        config: paramiko.SSHConfig,
    ) -> Optional[Connection]:
        """Convert a single SSH config host entry to a :class:`Connection`."""
        opts = config.lookup(alias)

        # hostname resolves to the alias itself if not explicitly set
        hostname: str = opts.get("hostname", alias)
        if not hostname or _WILDCARD_RE.search(hostname):
            return None

        user: str = opts.get("user", os.environ.get("USER", ""))
        if not user:
            return None

        port: int = 22
        raw_port = opts.get("port")
        if raw_port is not None:
            try:
                port = int(raw_port)
            except (TypeError, ValueError):
                pass

        key_path: Optional[str] = self._resolve_key(opts.get("identityfile"))
        tunnel: Optional[TunnelConfig] = self._parse_proxyjump(opts.get("proxyjump"), config)

        try:
            conn = Connection(
                name=alias,
                host=hostname,
                user=user,
                port=port,
                key_path=key_path,
                tunnel=tunnel,
            )
        except ValueError:
            return None

        return conn

    def _resolve_key(self, identity_file: "str | list[str] | None") -> Optional[str]:
        """Resolve IdentityFile to an absolute path string, or None."""
        if identity_file is None:
            return None
        # paramiko may return a list if multiple IdentityFile lines are present
        if isinstance(identity_file, list):
            identity_file = identity_file[0] if identity_file else None
        if not identity_file:
            return None
        expanded = os.path.expanduser(os.path.expandvars(identity_file))
        abs_path = str(Path(expanded).resolve())
        return abs_path

    def _parse_proxyjump(
        self,
        proxyjump: Optional[str],
        config: paramiko.SSHConfig,
    ) -> Optional[TunnelConfig]:
        """Convert a ProxyJump value (single hop) to a :class:`TunnelConfig`.

        Only the first hop is used when multiple comma-separated hops are given.
        """
        if not proxyjump:
            return None

        # Take only the first jump host (chained jumps not supported)
        first_jump = proxyjump.split(",")[0].strip()
        if not first_jump or first_jump.lower() == "none":
            return None

        # Parse [user@]host[:port]
        jump_user, jump_host, jump_port = self._parse_host_string(first_jump)

        # Look up further details from SSH config for the jump host
        jump_opts = config.lookup(jump_host)
        resolved_host: str = jump_opts.get("hostname", jump_host)
        if not jump_user:
            jump_user = jump_opts.get("user", os.environ.get("USER", ""))
        if jump_port == 22:
            raw_port = jump_opts.get("port")
            if raw_port is not None:
                try:
                    jump_port = int(raw_port)
                except (TypeError, ValueError):
                    pass
        jump_key = self._resolve_key(jump_opts.get("identityfile"))

        if not resolved_host or not jump_user:
            return None

        try:
            return TunnelConfig(
                host=resolved_host,
                user=jump_user,
                port=jump_port,
                key_path=jump_key,
            )
        except ValueError:
            return None

    @staticmethod
    def _parse_host_string(value: str) -> tuple[str, str, int]:
        """Parse ``[user@]host[:port]`` into ``(user, host, port)``."""
        user = ""
        port = 22

        if "@" in value:
            user, value = value.rsplit("@", 1)

        # Handle IPv6 bracket notation [::1]:22
        if value.startswith("["):
            bracket_end = value.find("]")
            if bracket_end != -1:
                host = value[1:bracket_end]
                rest = value[bracket_end + 1:]
                if rest.startswith(":"):
                    try:
                        port = int(rest[1:])
                    except ValueError:
                        pass
                return user, host, port

        if ":" in value:
            parts = value.rsplit(":", 1)
            try:
                port = int(parts[1])
                value = parts[0]
            except ValueError:
                pass  # no port suffix, keep as-is

        return user, value, port
