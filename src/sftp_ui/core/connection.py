"""
Connection dataclass and persistent store (JSON-backed).

Supported protocols
-------------------
* ``"sftp"``  — SSH/SFTP (default); uses ``host``, ``user``, ``port``, key / password.
* ``"s3"``    — Amazon S3 or S3-compatible storage (MinIO, Backblaze B2 …); uses
                ``cloud`` (a ``CloudConfig`` with provider ``"s3"``).
* ``"gcs"``   — Google Cloud Storage; uses ``cloud`` (provider ``"gcs"``).
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from sftp_ui.core.platform_utils import config_dir


DEFAULT_CONFIG_PATH = config_dir() / "connections.json"

# ── Optional keyring integration ──────────────────────────────────────────────
try:
    import keyring as _keyring
    _HAS_KEYRING = True
except ImportError:  # pragma: no cover
    _keyring = None  # type: ignore[assignment]
    _HAS_KEYRING = False

_KEYRING_SERVICE = "sftp-ui"
_KEYCHAIN_SENTINEL = "__keychain__"  # placeholder stored in JSON when keyring is used


@dataclass
class TunnelConfig:
    """SSH jump-host / tunnel configuration (local port-forward via paramiko)."""

    host: str                                      # jump-host address
    user: str                                      # jump-host username
    port: int = 22                                 # jump-host SSH port
    key_path: Optional[str] = None                 # absolute path to jump-host private key
    key_passphrase: Optional[str] = None           # passphrase for encrypted jump key
    password: Optional[str] = None                 # fallback password for jump host

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("tunnel host must not be empty")
        if not self.user:
            raise ValueError("tunnel user must not be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError(f"tunnel port must be 1-65535, got {self.port}")
        if self.key_path is not None and not os.path.isabs(self.key_path):
            raise ValueError(f"tunnel key_path must be absolute, got {self.key_path!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TunnelConfig":
        return cls(**data)


_CLOUD_KNOWN_FIELDS = {
    "provider", "bucket", "region", "access_key",
    "secret_key", "endpoint_url", "prefix",
}


@dataclass
class CloudConfig:
    """S3 / GCS cloud storage configuration.

    Attributes
    ----------
    provider:
        ``"s3"`` for Amazon S3 or any S3-compatible API (MinIO, Backblaze B2,
        DigitalOcean Spaces …), ``"gcs"`` for Google Cloud Storage.
    bucket:
        Target bucket name.  Required; must not be empty.
    region:
        AWS region (e.g. ``"us-east-1"``).  Optional for custom endpoints.
    access_key:
        AWS Access Key ID / HMAC key ID for GCS.
    secret_key:
        AWS Secret Access Key / HMAC secret for GCS.
    endpoint_url:
        Custom S3-compatible endpoint URL (e.g. ``"https://s3.us-west-000.backblazeb2.com"``).
        Leave empty for official AWS S3.
    prefix:
        Optional path prefix within the bucket (e.g. ``"backups/prod/"``).
        All operations will be rooted at this prefix.
    """

    provider: str                      # "s3" | "gcs"
    bucket: str                        # bucket name (required)
    region: str = ""                   # AWS region / GCS location
    access_key: str = ""               # access key / HMAC key ID
    secret_key: str = ""               # secret key / HMAC secret
    endpoint_url: str = ""             # custom S3-compatible endpoint
    prefix: str = ""                   # path prefix within the bucket

    def __post_init__(self) -> None:
        if self.provider not in ("s3", "gcs"):
            raise ValueError(f"provider must be 's3' or 'gcs', got {self.provider!r}")
        if not self.bucket:
            raise ValueError("bucket must not be empty")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CloudConfig":
        data = {k: v for k, v in data.items() if k in _CLOUD_KNOWN_FIELDS}
        return cls(**data)


_CONN_KNOWN_FIELDS = {
    "name", "host", "user", "port", "key_path",
    "key_passphrase", "password", "tunnel", "id",
    "favorite", "group", "last_connected", "protocol", "cloud", "use_agent",
}


@dataclass
class Connection:
    name: str
    host: str = ""                           # empty for cloud-only connections
    user: str = ""                           # empty for cloud-only connections
    port: int = 22
    key_path: Optional[str] = None           # absolute path to private key
    key_passphrase: Optional[str] = None     # passphrase for encrypted private key
    password: Optional[str] = None           # fallback SSH password (stored in plain)
    use_agent: bool = False                  # authenticate via SSH agent (ssh-agent / Keychain)
    tunnel: Optional[TunnelConfig] = None    # optional SSH jump-host tunnel
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    favorite: bool = False                   # pinned to top of connection list
    group: str = ""                          # freeform group / tag label
    last_connected: float = 0.0             # unix timestamp of last successful connect
    protocol: str = "sftp"                  # "sftp" | "s3" | "gcs"
    cloud: Optional[CloudConfig] = None     # set when protocol is "s3" or "gcs"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")
        if self.protocol not in ("sftp", "s3", "gcs"):
            raise ValueError(f"protocol must be 'sftp', 's3', or 'gcs', got {self.protocol!r}")
        if self.protocol == "sftp":
            if not self.host:
                raise ValueError("host must not be empty for SFTP connections")
            if not self.user:
                raise ValueError("user must not be empty for SFTP connections")
            if not (1 <= self.port <= 65535):
                raise ValueError(f"port must be 1-65535, got {self.port}")
            if self.key_path is not None and not os.path.isabs(self.key_path):
                raise ValueError(f"key_path must be absolute, got {self.key_path!r}")
        else:
            if self.cloud is None:
                raise ValueError(f"cloud config required for protocol {self.protocol!r}")

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict recursively converts nested dataclasses; keep as nested dicts
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Connection":
        data = {k: v for k, v in data.items() if k in _CONN_KNOWN_FIELDS}
        tunnel_data = data.pop("tunnel", None)
        cloud_data = data.pop("cloud", None)
        # Build cloud / tunnel before calling the constructor so __post_init__
        # receives them and can validate correctly.
        if cloud_data is not None:
            data["cloud"] = CloudConfig.from_dict(cloud_data)
        conn = cls(**data)
        if tunnel_data is not None:
            conn.tunnel = TunnelConfig.from_dict(tunnel_data)
        return conn


class ConnectionStore:
    """Load/save connections from a JSON file."""

    def __init__(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        self._path = Path(path)
        self._connections: dict[str, Connection] = {}
        self._load()

    # ── public API ──────────────────────────────────────────────────────────

    def all(self) -> list[Connection]:
        return list(self._connections.values())

    def get(self, connection_id: str) -> Connection:
        try:
            return self._connections[connection_id]
        except KeyError:
            raise KeyError(f"No connection with id {connection_id!r}")

    def add(self, conn: Connection) -> None:
        if conn.id in self._connections:
            raise ValueError(f"Connection id {conn.id!r} already exists")
        self._connections[conn.id] = conn
        self._save()

    def update(self, conn: Connection) -> None:
        if conn.id not in self._connections:
            raise KeyError(f"No connection with id {conn.id!r}")
        self._connections[conn.id] = conn
        self._save()

    def remove(self, connection_id: str) -> None:
        if connection_id not in self._connections:
            raise KeyError(f"No connection with id {connection_id!r}")
        del self._connections[connection_id]
        self._save()

    def find_by_name(self, name: str) -> Optional[Connection]:
        for c in self._connections.values():
            if c.name == name:
                return c
        return None

    def record_connected(self, connection_id: str) -> None:
        """Stamp last_connected with the current time and persist."""
        conn = self.get(connection_id)
        updated = dataclasses.replace(conn, last_connected=time.time())
        self._connections[connection_id] = updated
        self._save()

    # ── private ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in raw:
            try:
                c = Connection.from_dict(item)
                # Resolve keychain sentinels back to plaintext secrets
                if _HAS_KEYRING:
                    if c.password == _KEYCHAIN_SENTINEL:
                        try:
                            c.password = _keyring.get_password(_KEYRING_SERVICE, f"{c.id}:password")
                        except Exception:
                            c.password = None
                    if c.key_passphrase == _KEYCHAIN_SENTINEL:
                        try:
                            c.key_passphrase = _keyring.get_password(_KEYRING_SERVICE, f"{c.id}:key_passphrase")
                        except Exception:
                            c.key_passphrase = None
                self._connections[c.id] = c
            except (TypeError, ValueError):
                continue  # skip corrupt entries

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for c in self._connections.values():
            d = c.to_dict()
            if _HAS_KEYRING:
                if d.get("password") and d["password"] != _KEYCHAIN_SENTINEL:
                    try:
                        _keyring.set_password(_KEYRING_SERVICE, f"{c.id}:password", d["password"])
                        d["password"] = _KEYCHAIN_SENTINEL
                    except Exception:
                        pass  # fall back to storing in plain text
                if d.get("key_passphrase") and d["key_passphrase"] != _KEYCHAIN_SENTINEL:
                    try:
                        _keyring.set_password(_KEYRING_SERVICE, f"{c.id}:key_passphrase", d["key_passphrase"])
                        d["key_passphrase"] = _KEYCHAIN_SENTINEL
                    except Exception:
                        pass
            data.append(d)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
