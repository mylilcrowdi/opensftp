"""
License management for Pro/Free feature gating.

License key is stored as JSON in ~/.config/sftp-ui/license.key:
    {"key": "PRO-XXXX-XXXX-XXXX", "email": "...", "activated_at": 1234567890}

Usage:
    from sftp_ui.core.license import is_pro, LicenseManager

    if is_pro():
        # Pro feature code
        ...

    @pro_required(license_manager)
    def my_pro_feature():
        ...
"""
from __future__ import annotations

import json
import re
import time
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from sftp_ui.core.platform_utils import config_dir


class LicenseStatus(Enum):
    FREE = "free"
    PRO = "pro"


_KEY_PATTERN = re.compile(r"^SFTP-[A-F0-9]{8}-[A-F0-9]{8}-[A-F0-9]{8}-[A-F0-9]{8}$")


def _license_path() -> Path:
    return config_dir() / "license.key"


def _read_license(path: Path) -> Optional[dict]:
    """Read and parse a license key file. Returns None on any error."""
    try:
        text = path.read_text().strip()
        if not text:
            return None
        data = json.loads(text)
        if not isinstance(data, dict) or "key" not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def is_pro() -> bool:
    """Quick check: is the current installation Pro-licensed?"""
    data = _read_license(_license_path())
    return data is not None and _KEY_PATTERN.match(data.get("key", "")) is not None


class LicenseManager:
    """Manages license activation, validation, and status."""

    def __init__(self, key_path: Optional[Path] = None) -> None:
        self.key_path = key_path or _license_path()

    def status(self) -> LicenseStatus:
        data = _read_license(self.key_path)
        if data and _KEY_PATTERN.match(data.get("key", "")):
            return LicenseStatus.PRO
        return LicenseStatus.FREE

    def validate_key(self, key: Optional[str]) -> bool:
        if not key:
            return False
        return _KEY_PATTERN.match(key) is not None

    def activate(self, key: str, email: str) -> None:
        if not self.validate_key(key):
            raise ValueError(f"Invalid license key format: {key}")
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "key": key,
            "email": email,
            "activated_at": int(time.time()),
        }
        self.key_path.write_text(json.dumps(data, indent=2))

    def deactivate(self) -> None:
        if self.key_path.exists():
            self.key_path.unlink()


def pro_required(
    manager: LicenseManager,
    on_blocked: Optional[Callable[[], None]] = None,
):
    """Decorator that gates a function behind Pro license."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if manager.status() == LicenseStatus.PRO:
                return func(*args, **kwargs)
            if on_blocked:
                on_blocked()
            return None
        return wrapper
    return decorator
