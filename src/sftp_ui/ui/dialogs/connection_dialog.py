"""
ConnectionDialog — add or edit an SFTP / S3 / GCS connection.

Visual structure (SFTP mode):
  ┌──────────────────────────────────────┐
  │  New Connection / Edit Connection    │
  ├──────────────────────────────────────┤
  │  Protocol     [SFTP ▾]               │
  │  Name *       [________________]     │
  │  Host *       [________________]     │
  │  User *       [________________]     │
  │  Port         [22         ]          │
  │  Group        [________________]     │
  │  SSH Key      [________] [Browse…]   │
  │  Key Passphrase [·······]            │
  │  Password     [········]             │
  │  ☐ Favorite                         │
  ├──────────────────────────────────────┤
  │  ☐ Use SSH Tunnel (Jump Host)        │
  │  ┌─────────────────────────────────┐ │
  │  │ …tunnel fields…                 │ │
  │  └─────────────────────────────────┘ │
  ├──────────────────────────────────────┤
  │  [error label if needed]             │
  ├──────────────────────────────────────┤
  │                    [Cancel]  [Save]  │
  └──────────────────────────────────────┘

Visual structure (S3 / GCS mode):
  ┌──────────────────────────────────────┐
  │  New Connection / Edit Connection    │
  ├──────────────────────────────────────┤
  │  Protocol     [Amazon S3 ▾]          │
  │  Name *       [________________]     │
  │  Group        [________________]     │
  │  ☐ Favorite                         │
  ├── S3 / Cloud Settings ───────────────┤
  │  Bucket *     [________________]     │
  │  Region       [________________]     │
  │  Access Key   [________________]     │
  │  Secret Key   [········]             │
  │  Endpoint URL [________________]     │
  │  Path Prefix  [________________]     │
  ├──────────────────────────────────────┤
  │  [error label if needed]             │
  ├──────────────────────────────────────┤
  │                    [Cancel]  [Save]  │
  └──────────────────────────────────────┘
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from sftp_ui.core.connection import CloudConfig, Connection, ConnectionStore, TunnelConfig


# ── Protocol labels / identifiers ─────────────────────────────────────────────

_PROTOCOL_ITEMS: list[tuple[str, str]] = [
    ("sftp", "SFTP (SSH File Transfer)"),
    ("s3",   "Amazon S3 / S3-Compatible"),
    ("gcs",  "Google Cloud Storage"),
]
_PROTOCOL_IDS   = [pid for pid, _ in _PROTOCOL_ITEMS]
_PROTOCOL_LABELS = [lbl for _, lbl in _PROTOCOL_ITEMS]


def _make_password_row(line_edit: QLineEdit) -> QHBoxLayout:
    """Wrap a password QLineEdit in a HBoxLayout with a show/hide eye toggle."""
    row = QHBoxLayout()
    row.setSpacing(4)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(line_edit)
    toggle = QPushButton("👁")
    toggle.setFixedWidth(30)
    toggle.setFixedHeight(line_edit.sizeHint().height())
    toggle.setCheckable(True)
    toggle.setToolTip("Show / hide password")
    toggle.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def _on_toggle(checked: bool) -> None:
        line_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    toggle.toggled.connect(_on_toggle)
    row.addWidget(toggle)
    return row


class ConnectionDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        conn: Optional[Connection] = None,
        store: Optional[ConnectionStore] = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._store = store
        self.setWindowTitle("Edit Connection" if conn else "New Connection")
        self.setMinimumWidth(480)
        self.setModal(True)
        self._result_conn: Optional[Connection] = None
        self._build_ui()
        if conn:
            self._populate(conn)
        self._install_group_completer()
        self._name.setFocus()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(24, 20, 24, 20)

        # title
        title = QLabel("Edit Connection" if self._conn else "New Connection")
        title.setStyleSheet("font-size: 16px; font-weight: 700; padding-bottom: 4px;")
        root.addWidget(title)

        # ── protocol selector ─────────────────────────────────────────────────
        proto_row = QFormLayout()
        proto_row.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        proto_row.setSpacing(8)
        root.addLayout(proto_row)

        self._protocol_combo = QComboBox()
        for _pid, lbl in _PROTOCOL_ITEMS:
            self._protocol_combo.addItem(lbl)
        self._protocol_combo.setToolTip(
            "Choose the connection protocol.\n"
            "SFTP — classic SSH file transfer.\n"
            "S3 — Amazon S3 or any S3-compatible storage (MinIO, Backblaze B2 …).\n"
            "GCS — Google Cloud Storage."
        )
        self._protocol_combo.currentIndexChanged.connect(self._on_protocol_changed)
        proto_row.addRow("Protocol", self._protocol_combo)

        # ── shared name / group / favorite ────────────────────────────────────
        shared_form = QFormLayout()
        shared_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        shared_form.setSpacing(8)
        root.addLayout(shared_form)

        self._name = QLineEdit()
        self._name.setPlaceholderText("My Server")

        self._group = QLineEdit()
        self._group.setPlaceholderText("e.g. Production, Dev (optional)")

        self._favorite = QCheckBox("Mark as favorite (pinned at top of connection list)")

        shared_form.addRow("Name *", self._name)
        shared_form.addRow("Group", self._group)
        shared_form.addRow("", self._favorite)

        # ── SFTP section ──────────────────────────────────────────────────────
        self._sftp_group = QGroupBox("SFTP Settings")
        self._sftp_group.setStyleSheet(
            "QGroupBox { font-weight: 600; border: 1px solid palette(mid);"
            " border-radius: 4px; margin-top: 8px; padding: 12px 8px 8px 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        sftp_layout = QVBoxLayout(self._sftp_group)
        sftp_layout.setContentsMargins(8, 4, 8, 8)
        sftp_layout.setSpacing(6)
        root.addWidget(self._sftp_group)

        sftp_form = QFormLayout()
        sftp_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        sftp_form.setSpacing(8)
        sftp_layout.addLayout(sftp_form)

        self._host = QLineEdit()
        self._host.setPlaceholderText("192.168.1.1 or example.com (or host:port)")
        self._host.textEdited.connect(self._on_host_edited)

        self._user = QLineEdit()
        self._user.setPlaceholderText("root")

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(22)
        self._port.setFixedWidth(90)
        self._port.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # SSH key row
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self._key_path = QLineEdit()
        self._key_path.setPlaceholderText("Leave empty to use password")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_key)
        key_row.addWidget(self._key_path)
        key_row.addWidget(browse_btn)

        self._key_passphrase = QLineEdit()
        self._key_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_passphrase.setPlaceholderText("Leave empty if key is not encrypted")

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Used only if no key is set")

        self._use_agent = QCheckBox("Use SSH Agent (ssh-agent / macOS Keychain / Pageant)")

        # Keepalive spinbox: 0 = disabled, 1–3600 s
        keepalive_row = QHBoxLayout()
        keepalive_row.setSpacing(6)
        self._keepalive_interval = QSpinBox()
        self._keepalive_interval.setRange(0, 3600)
        self._keepalive_interval.setValue(30)
        self._keepalive_interval.setSuffix(" s")
        self._keepalive_interval.setFixedWidth(100)
        self._keepalive_interval.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._keepalive_interval.setToolTip(
            "Send a keepalive packet to the server every N seconds.\n"
            "Prevents the server (and NAT routers) from dropping idle connections.\n"
            "Set to 0 to disable keepalives entirely."
        )
        keepalive_hint = QLabel("seconds  (0 = disabled)")
        keepalive_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        keepalive_row.addWidget(self._keepalive_interval)
        keepalive_row.addWidget(keepalive_hint)
        keepalive_row.addStretch()

        sftp_form.addRow("Host *", self._host)
        sftp_form.addRow("User *", self._user)
        sftp_form.addRow("Port", self._port)
        sftp_form.addRow("SSH Key", key_row)
        sftp_form.addRow("Key Passphrase", _make_password_row(self._key_passphrase))
        sftp_form.addRow("Password", _make_password_row(self._password))
        sftp_form.addRow("", self._use_agent)
        sftp_form.addRow("Keepalive", keepalive_row)

        # ── SSH tunnel section ────────────────────────────────────────────────
        self._tunnel_checkbox = QCheckBox("Use SSH Tunnel (Jump Host)")
        self._tunnel_checkbox.setStyleSheet("font-weight: 600; margin-top: 4px;")
        sftp_layout.addWidget(self._tunnel_checkbox)

        self._tunnel_group = QGroupBox()
        self._tunnel_group.setStyleSheet(
            "QGroupBox { border: 1px solid palette(mid); border-radius: 4px;"
            " margin-top: 2px; padding: 8px; }"
        )
        self._tunnel_group.setVisible(False)
        sftp_layout.addWidget(self._tunnel_group)

        tunnel_layout = QVBoxLayout(self._tunnel_group)
        tunnel_layout.setContentsMargins(8, 8, 8, 8)
        tunnel_layout.setSpacing(6)

        tunnel_form = QFormLayout()
        tunnel_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        tunnel_form.setSpacing(8)
        tunnel_layout.addLayout(tunnel_form)

        self._tunnel_host = QLineEdit()
        self._tunnel_host.setPlaceholderText("bastion.example.com")

        self._tunnel_user = QLineEdit()
        self._tunnel_user.setPlaceholderText("ec2-user")

        self._tunnel_port = QSpinBox()
        self._tunnel_port.setRange(1, 65535)
        self._tunnel_port.setValue(22)
        self._tunnel_port.setFixedWidth(90)
        self._tunnel_port.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Tunnel SSH key row
        tunnel_key_row = QHBoxLayout()
        tunnel_key_row.setSpacing(6)
        self._tunnel_key_path = QLineEdit()
        self._tunnel_key_path.setPlaceholderText("Leave empty to use password")
        tunnel_browse_btn = QPushButton("Browse…")
        tunnel_browse_btn.setFixedWidth(90)
        tunnel_browse_btn.clicked.connect(self._browse_tunnel_key)
        tunnel_key_row.addWidget(self._tunnel_key_path)
        tunnel_key_row.addWidget(tunnel_browse_btn)

        self._tunnel_key_passphrase = QLineEdit()
        self._tunnel_key_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self._tunnel_key_passphrase.setPlaceholderText("Leave empty if key is not encrypted")

        self._tunnel_password = QLineEdit()
        self._tunnel_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._tunnel_password.setPlaceholderText("Used only if no tunnel key is set")

        tunnel_form.addRow("Tunnel Host *", self._tunnel_host)
        tunnel_form.addRow("Tunnel User *", self._tunnel_user)
        tunnel_form.addRow("Tunnel Port", self._tunnel_port)
        tunnel_form.addRow("Tunnel Key", tunnel_key_row)
        tunnel_form.addRow("Key Passphrase", _make_password_row(self._tunnel_key_passphrase))
        tunnel_form.addRow("Password", _make_password_row(self._tunnel_password))

        def _toggle_tunnel(checked: bool) -> None:
            self._tunnel_group.setVisible(checked)
            self.adjustSize()

        self._tunnel_checkbox.toggled.connect(_toggle_tunnel)

        # ── Cloud section (S3 / GCS) ──────────────────────────────────────────
        self._cloud_group = QGroupBox("Cloud Storage Settings")
        self._cloud_group.setStyleSheet(
            "QGroupBox { font-weight: 600; border: 1px solid palette(mid);"
            " border-radius: 4px; margin-top: 8px; padding: 12px 8px 8px 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        self._cloud_group.setVisible(False)
        cloud_layout = QVBoxLayout(self._cloud_group)
        cloud_layout.setContentsMargins(8, 4, 8, 8)
        root.addWidget(self._cloud_group)

        cloud_form = QFormLayout()
        cloud_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        cloud_form.setSpacing(8)
        cloud_layout.addLayout(cloud_form)

        self._cloud_bucket = QLineEdit()
        self._cloud_bucket.setPlaceholderText("my-bucket-name")

        self._cloud_region = QLineEdit()
        self._cloud_region.setPlaceholderText("us-east-1  (leave empty for default)")

        self._cloud_access_key = QLineEdit()
        self._cloud_access_key.setPlaceholderText("AWS Access Key ID or HMAC Key")

        self._cloud_secret_key = QLineEdit()
        self._cloud_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._cloud_secret_key.setPlaceholderText("AWS Secret Access Key")

        self._cloud_endpoint_url = QLineEdit()
        self._cloud_endpoint_url.setPlaceholderText(
            "https://… (leave empty for AWS S3)"
        )
        self._cloud_endpoint_url.setToolTip(
            "Custom S3-compatible endpoint.\n"
            "Examples:\n"
            "  MinIO:       https://minio.example.com\n"
            "  Backblaze B2: https://s3.us-west-004.backblazeb2.com\n"
            "  DO Spaces:   https://<region>.digitaloceanspaces.com"
        )

        self._cloud_prefix = QLineEdit()
        self._cloud_prefix.setPlaceholderText(
            "backups/prod/  (optional path prefix within the bucket)"
        )

        cloud_form.addRow("Bucket *", self._cloud_bucket)
        cloud_form.addRow("Region", self._cloud_region)
        cloud_form.addRow("Access Key", self._cloud_access_key)
        cloud_form.addRow("Secret Key", _make_password_row(self._cloud_secret_key))
        cloud_form.addRow("Endpoint URL", self._cloud_endpoint_url)
        cloud_form.addRow("Path Prefix", self._cloud_prefix)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # error label
        self._error_label = QLabel("")
        self._error_label.setObjectName("error")
        self._error_label.setWordWrap(True)
        root.addWidget(self._error_label)

        # buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Save,
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setObjectName("primary")
            save_btn.setDefault(True)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── protocol switching ────────────────────────────────────────────────────

    def _on_protocol_changed(self, index: int) -> None:
        """Show/hide the SFTP or Cloud section depending on the chosen protocol."""
        protocol = _PROTOCOL_IDS[index] if 0 <= index < len(_PROTOCOL_IDS) else "sftp"
        is_sftp = protocol == "sftp"
        self._sftp_group.setVisible(is_sftp)
        self._cloud_group.setVisible(not is_sftp)

        # Update the cloud group box title to reflect the protocol
        if protocol == "s3":
            self._cloud_group.setTitle("Amazon S3 / S3-Compatible Settings")
        elif protocol == "gcs":
            self._cloud_group.setTitle("Google Cloud Storage Settings")

        self.adjustSize()

    def _current_protocol(self) -> str:
        idx = self._protocol_combo.currentIndex()
        return _PROTOCOL_IDS[idx] if 0 <= idx < len(_PROTOCOL_IDS) else "sftp"

    # ── helpers ───────────────────────────────────────────────────────────────

    def _populate(self, conn: Connection) -> None:
        # Set protocol combo first so the right section is shown
        protocol = conn.protocol if conn.protocol in _PROTOCOL_IDS else "sftp"
        self._protocol_combo.setCurrentIndex(_PROTOCOL_IDS.index(protocol))

        self._name.setText(conn.name)
        self._group.setText(conn.group or "")
        self._favorite.setChecked(conn.favorite)

        if protocol == "sftp":
            self._host.setText(conn.host)
            self._user.setText(conn.user)
            self._port.setValue(conn.port)
            if conn.key_path:
                self._key_path.setText(conn.key_path)
            if conn.key_passphrase:
                self._key_passphrase.setText(conn.key_passphrase)
            if conn.password:
                self._password.setText(conn.password)
            self._use_agent.setChecked(conn.use_agent)
            self._keepalive_interval.setValue(conn.keepalive_interval)

            if conn.tunnel is not None:
                self._tunnel_checkbox.setChecked(True)
                self._tunnel_group.setVisible(True)
                self._tunnel_host.setText(conn.tunnel.host)
                self._tunnel_user.setText(conn.tunnel.user)
                self._tunnel_port.setValue(conn.tunnel.port)
                if conn.tunnel.key_path:
                    self._tunnel_key_path.setText(conn.tunnel.key_path)
                if conn.tunnel.key_passphrase:
                    self._tunnel_key_passphrase.setText(conn.tunnel.key_passphrase)
                if conn.tunnel.password:
                    self._tunnel_password.setText(conn.tunnel.password)
        else:
            # Cloud connection
            if conn.cloud is not None:
                self._cloud_bucket.setText(conn.cloud.bucket)
                self._cloud_region.setText(conn.cloud.region)
                self._cloud_access_key.setText(conn.cloud.access_key)
                self._cloud_secret_key.setText(conn.cloud.secret_key)
                self._cloud_endpoint_url.setText(conn.cloud.endpoint_url)
                self._cloud_prefix.setText(conn.cloud.prefix)

    def _on_host_edited(self, text: str) -> None:
        """Split 'host:port' pasted into the Host field and populate Port."""
        text = text.strip()
        if ":" in text and not text.startswith("["):
            host_part, _, port_part = text.rpartition(":")
            if host_part and port_part.isdigit():
                port_val = int(port_part)
                if 1 <= port_val <= 65535:
                    self._host.blockSignals(True)
                    self._host.setText(host_part)
                    self._host.blockSignals(False)
                    self._port.setValue(port_val)

    def _browse_key(self) -> None:
        settings = QSettings("sftp-ui", "sftp-ui")
        start_dir = settings.value("browse/last_key_dir", str(Path.home() / ".ssh"))
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key",
            start_dir,
            "All Files (*)",
        )
        if path:
            self._key_path.setText(path)
            settings.setValue("browse/last_key_dir", str(Path(path).parent))

    def _browse_tunnel_key(self) -> None:
        settings = QSettings("sftp-ui", "sftp-ui")
        start_dir = settings.value("browse/last_key_dir", str(Path.home() / ".ssh"))
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Tunnel SSH Private Key",
            start_dir,
            "All Files (*)",
        )
        if path:
            self._tunnel_key_path.setText(path)
            settings.setValue("browse/last_key_dir", str(Path(path).parent))

    def _build_tunnel(self) -> Optional[TunnelConfig]:
        """Build a TunnelConfig from the tunnel form fields, or return None."""
        if not self._tunnel_checkbox.isChecked():
            return None
        try:
            return TunnelConfig(
                host=self._tunnel_host.text().strip(),
                user=self._tunnel_user.text().strip(),
                port=self._tunnel_port.value(),
                key_path=self._tunnel_key_path.text().strip() or None,
                key_passphrase=self._tunnel_key_passphrase.text() or None,
                password=self._tunnel_password.text() or None,
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    def _set_field_error(self, widget: "QLineEdit", has_error: bool) -> None:
        """Apply or clear a red-border highlight on a required field."""
        widget.setProperty("inputError", has_error)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _install_group_completer(self) -> None:
        """Attach a QCompleter to the Group field using existing group names from the store."""
        if self._store is None:
            return
        groups = sorted({
            c.group for c in self._store.all()
            if c.group
        })
        if not groups:
            return
        completer = QCompleter(groups, self._group)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.InlineCompletion)
        self._group.setCompleter(completer)

    # ── validation & accept ───────────────────────────────────────────────────

    def _on_accept(self) -> None:
        self._error_label.setText("")
        protocol = self._current_protocol()

        # ── Name (always required) ─────────────────────────────────────────────
        name_empty = not self._name.text().strip()
        self._set_field_error(self._name, name_empty)
        if name_empty:
            self._error_label.setText("Name is required.")
            return

        try:
            if protocol == "sftp":
                conn = self._accept_sftp()
            else:
                conn = self._accept_cloud(protocol)
        except ValueError as exc:
            self._error_label.setText(str(exc))
            return

        self._result_conn = conn
        self.accept()

    def _accept_sftp(self) -> Connection:
        """Validate SFTP fields and return a Connection (raises ValueError on error)."""
        host_empty = not self._host.text().strip()
        user_empty = not self._user.text().strip()
        for widget, empty in (
            (self._host, host_empty),
            (self._user, user_empty),
        ):
            self._set_field_error(widget, empty)
        if host_empty or user_empty:
            raise ValueError("Host and User are required.")

        if self._tunnel_checkbox.isChecked():
            tunnel_host_empty = not self._tunnel_host.text().strip()
            tunnel_user_empty = not self._tunnel_user.text().strip()
            for widget, empty in (
                (self._tunnel_host, tunnel_host_empty),
                (self._tunnel_user, tunnel_user_empty),
            ):
                self._set_field_error(widget, empty)
            if tunnel_host_empty or tunnel_user_empty:
                raise ValueError("Tunnel Host and Tunnel User are required.")
        else:
            self._set_field_error(self._tunnel_host, False)
            self._set_field_error(self._tunnel_user, False)

        key_text = self._key_path.text().strip()
        if key_text and not Path(key_text).exists():
            self._set_field_error(self._key_path, True)
            raise ValueError(f"SSH key not found: {key_text}")
        self._set_field_error(self._key_path, False)

        tunnel_key_text = (
            self._tunnel_key_path.text().strip()
            if self._tunnel_checkbox.isChecked()
            else ""
        )
        if tunnel_key_text and not Path(tunnel_key_text).exists():
            self._set_field_error(self._tunnel_key_path, True)
            raise ValueError(f"Tunnel SSH key not found: {tunnel_key_text}")
        if self._tunnel_checkbox.isChecked():
            self._set_field_error(self._tunnel_key_path, False)

        tunnel = self._build_tunnel()
        return Connection(
            name=self._name.text().strip(),
            host=self._host.text().strip(),
            user=self._user.text().strip(),
            port=self._port.value(),
            group=self._group.text().strip(),
            key_path=self._key_path.text().strip() or None,
            key_passphrase=self._key_passphrase.text() or None,
            password=self._password.text() or None,
            tunnel=tunnel,
            favorite=self._favorite.isChecked(),
            protocol="sftp",
            id=self._conn.id if self._conn else str(uuid.uuid4()),
            last_connected=self._conn.last_connected if self._conn else 0.0,
            keepalive_interval=self._keepalive_interval.value(),
        )

    def _accept_cloud(self, protocol: str) -> Connection:
        """Validate cloud fields and return a Connection (raises ValueError on error)."""
        bucket = self._cloud_bucket.text().strip()
        if not bucket:
            self._set_field_error(self._cloud_bucket, True)
            raise ValueError("Bucket name is required.")
        self._set_field_error(self._cloud_bucket, False)

        try:
            cloud = CloudConfig(
                provider=protocol,
                bucket=bucket,
                region=self._cloud_region.text().strip(),
                access_key=self._cloud_access_key.text().strip(),
                secret_key=self._cloud_secret_key.text().strip(),
                endpoint_url=self._cloud_endpoint_url.text().strip(),
                prefix=self._cloud_prefix.text().strip(),
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        return Connection(
            name=self._name.text().strip(),
            protocol=protocol,
            cloud=cloud,
            group=self._group.text().strip(),
            favorite=self._favorite.isChecked(),
            id=self._conn.id if self._conn else str(uuid.uuid4()),
            last_connected=self._conn.last_connected if self._conn else 0.0,
        )

    def result_connection(self) -> Connection:
        assert self._result_conn is not None
        return self._result_conn
