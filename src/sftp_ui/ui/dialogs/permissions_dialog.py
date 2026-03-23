"""
PermissionsDialog — chmod editor for remote files/directories.

Triggered from the RemotePanel context menu ("Permissions…").
Displays:
  - File name + full path (read-only header)
  - 3×3 checkbox grid: Owner / Group / Other  ×  Read / Write / Execute
  - Special bits row: SUID / SGID / Sticky
  - Octal input that stays in sync with the checkboxes
  - Apply button that calls sftp.chmod(); Cancel closes without changes

Visual layout:
  ┌──────────────────────────────────────────┐
  │  🔒  Permissions                          │
  ├──────────────────────────────────────────┤
  │  /path/to/file.txt                        │
  ├──────────────────────────────────────────┤
  │           Owner   Group   Other           │
  │  Read      ☑      ☑      ☑              │
  │  Write     ☑      ☐      ☐              │
  │  Execute   ☑      ☑      ☑              │
  ├──────────────────────────────────────────┤
  │  Special bits:  □ SUID  □ SGID  □ Sticky │
  ├──────────────────────────────────────────┤
  │  Octal:  [ 0755 ]   Symbolic: rwxr-xr-x  │
  ├──────────────────────────────────────────┤
  │                    [Cancel]  [Apply]      │
  └──────────────────────────────────────────┘
"""
from __future__ import annotations

import stat as _stat
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# ── bit constants ──────────────────────────────────────────────────────────────

# (row_label, owner_bit, group_bit, other_bit)
_RWX_ROWS: list[tuple[str, int, int, int]] = [
    ("Read",    0o400, 0o040, 0o004),
    ("Write",   0o200, 0o020, 0o002),
    ("Execute", 0o100, 0o010, 0o001),
]

_SPECIAL_BITS: list[tuple[str, int]] = [
    ("SUID",   0o4000),
    ("SGID",   0o2000),
    ("Sticky", 0o1000),
]

_COL_LABELS = ("Owner", "Group", "Other")


def mode_to_symbolic(mode: int) -> str:
    """Convert a Unix permission integer to symbolic notation (e.g. ``rwxr-xr-x``)."""
    bits = [
        "r" if mode & 0o400 else "-",
        "w" if mode & 0o200 else "-",
        # SUID + execute
        ("s" if mode & 0o100 else "S") if mode & 0o4000 else ("x" if mode & 0o100 else "-"),
        "r" if mode & 0o040 else "-",
        "w" if mode & 0o020 else "-",
        # SGID + execute
        ("s" if mode & 0o010 else "S") if mode & 0o2000 else ("x" if mode & 0o010 else "-"),
        "r" if mode & 0o004 else "-",
        "w" if mode & 0o002 else "-",
        # Sticky + execute
        ("t" if mode & 0o001 else "T") if mode & 0o1000 else ("x" if mode & 0o001 else "-"),
    ]
    return "".join(bits)


def _divider() -> QFrame:
    """Return a thin horizontal separator line."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


# ── dialog ─────────────────────────────────────────────────────────────────────

class PermissionsDialog(QDialog):
    """Modal chmod editor.

    Parameters
    ----------
    path:
        Full remote path being edited (shown in the header).
    name:
        File/directory name (shown in title bar).
    initial_mode:
        Current permission bits (lower 12 bits of ``st_mode``).  Pass ``0``
        if unknown; the editor will start at ``0o000``.
    parent:
        Optional parent widget.
    """

    def __init__(
        self,
        path: str,
        name: str,
        initial_mode: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Permissions — {name}")
        self.setMinimumWidth(360)
        self.setModal(True)

        self._path = path
        self._name = name
        # Keep only the permission bits (strip file-type bits)
        self._mode: int = initial_mode & 0o7777

        # Signals blocked flag — prevents re-entrant updates
        self._updating: bool = False

        self._build_ui()
        self._load_mode(self._mode)

    # ── build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── Title row ─────────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        icon = QLabel("🔒")
        icon.setStyleSheet("font-size: 20px;")
        title_lbl = QLabel("Permissions")
        title_font = QFont(title_lbl.font())
        title_font.setPointSize(13)
        title_font.setBold(True)
        title_lbl.setFont(title_font)
        title_row.addWidget(icon)
        title_row.addWidget(title_lbl, stretch=1)
        root.addLayout(title_row)

        # ── Path label ────────────────────────────────────────────────────────
        path_lbl = QLabel(self._path)
        path_lbl.setObjectName("path-label")
        path_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet("color: #6c7086; font-size: 11px;")
        root.addWidget(path_lbl)

        root.addWidget(_divider())

        # ── rwx grid ──────────────────────────────────────────────────────────
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)

        # Column headers (Owner / Group / Other)
        for col_idx, col_lbl in enumerate(_COL_LABELS):
            hdr = QLabel(col_lbl)
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hdr.setStyleSheet("font-weight: 600; color: #cdd6f4;")
            grid.addWidget(hdr, 0, col_idx + 1)

        # Row header + 3 checkboxes per row
        # self._cb[row][col]  (row: 0=Read,1=Write,2=Exec; col: 0=Owner,1=Group,2=Other)
        self._cb: list[list[QCheckBox]] = []
        for row_idx, (row_lbl, *_bits) in enumerate(_RWX_ROWS):
            lbl = QLabel(row_lbl)
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            lbl.setStyleSheet("color: #cdd6f4;")
            grid.addWidget(lbl, row_idx + 1, 0)

            row_cbs: list[QCheckBox] = []
            for col_idx in range(3):
                cb = QCheckBox()
                cb.setStyleSheet("QCheckBox { margin: 0; padding: 0; }")
                cb.toggled.connect(self._on_checkbox_toggled)
                grid.addWidget(cb, row_idx + 1, col_idx + 1, Qt.AlignmentFlag.AlignHCenter)
                row_cbs.append(cb)
            self._cb.append(row_cbs)

        root.addWidget(grid_widget)
        root.addWidget(_divider())

        # ── Special bits ──────────────────────────────────────────────────────
        special_row = QHBoxLayout()
        special_lbl = QLabel("Special:")
        special_lbl.setStyleSheet("color: #cdd6f4; font-weight: 600;")
        special_row.addWidget(special_lbl)

        self._special_cb: list[QCheckBox] = []
        for label, _bit in _SPECIAL_BITS:
            cb = QCheckBox(label)
            cb.setStyleSheet("color: #cdd6f4;")
            cb.toggled.connect(self._on_checkbox_toggled)
            special_row.addWidget(cb)
            self._special_cb.append(cb)
        special_row.addStretch()
        root.addLayout(special_row)

        root.addWidget(_divider())

        # ── Octal + symbolic ──────────────────────────────────────────────────
        octal_row = QHBoxLayout()

        octal_lbl = QLabel("Octal:")
        octal_lbl.setStyleSheet("color: #cdd6f4; font-weight: 600;")
        octal_row.addWidget(octal_lbl)

        self._octal_edit = QLineEdit()
        self._octal_edit.setObjectName("octal-edit")
        self._octal_edit.setMaxLength(4)
        self._octal_edit.setFixedWidth(60)
        self._octal_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Only digits 0-7 (octal)
        self._octal_edit.setValidator(
            _OctalValidator(0, 0o7777, self._octal_edit)
        )
        self._octal_edit.textEdited.connect(self._on_octal_edited)
        octal_row.addWidget(self._octal_edit)

        octal_row.addSpacing(16)

        sym_prefix = QLabel("Symbolic:")
        sym_prefix.setStyleSheet("color: #cdd6f4; font-weight: 600;")
        octal_row.addWidget(sym_prefix)

        self._sym_lbl = QLabel("----------")
        self._sym_lbl.setObjectName("symbolic-label")
        mono_font = QFont("Courier New", 11)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self._sym_lbl.setFont(mono_font)
        self._sym_lbl.setStyleSheet("color: #a6e3a1;")
        octal_row.addWidget(self._sym_lbl)
        octal_row.addStretch()

        root.addLayout(octal_row)

        root.addWidget(_divider())

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("apply-btn")
        self._apply_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        btn_box.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.addButton(self._apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.rejected.connect(self.reject)
        btn_box.accepted.connect(self.accept)
        root.addWidget(btn_box)

    # ── Mode loading / reading ─────────────────────────────────────────────────

    def _load_mode(self, mode: int) -> None:
        """Push *mode* into all UI controls without triggering feedback loops."""
        self._updating = True
        try:
            # rwx checkboxes
            for row_idx, (_label, owner_bit, group_bit, other_bit) in enumerate(_RWX_ROWS):
                self._cb[row_idx][0].setChecked(bool(mode & owner_bit))
                self._cb[row_idx][1].setChecked(bool(mode & group_bit))
                self._cb[row_idx][2].setChecked(bool(mode & other_bit))

            # special bits
            for sp_idx, (_label, bit) in enumerate(_SPECIAL_BITS):
                self._special_cb[sp_idx].setChecked(bool(mode & bit))

            # octal text
            self._octal_edit.setText(f"{mode & 0o7777:04o}")

            # symbolic
            self._sym_lbl.setText(mode_to_symbolic(mode))
        finally:
            self._updating = False

    def current_mode(self) -> int:
        """Return the permission bits currently shown in the dialog."""
        mode = 0
        for row_idx, (_label, owner_bit, group_bit, other_bit) in enumerate(_RWX_ROWS):
            if self._cb[row_idx][0].isChecked():
                mode |= owner_bit
            if self._cb[row_idx][1].isChecked():
                mode |= group_bit
            if self._cb[row_idx][2].isChecked():
                mode |= other_bit
        for sp_idx, (_label, bit) in enumerate(_SPECIAL_BITS):
            if self._special_cb[sp_idx].isChecked():
                mode |= bit
        return mode

    # ── Slot: checkbox toggled ─────────────────────────────────────────────────

    def _on_checkbox_toggled(self, _checked: bool) -> None:
        if self._updating:
            return
        mode = self.current_mode()
        self._updating = True
        try:
            self._octal_edit.setText(f"{mode:04o}")
            self._sym_lbl.setText(mode_to_symbolic(mode))
        finally:
            self._updating = False

    # ── Slot: octal text edited ────────────────────────────────────────────────

    def _on_octal_edited(self, text: str) -> None:
        if self._updating:
            return
        # Only update checkboxes when we have a plausibly complete value
        try:
            value = int(text, 8) if text else 0
        except ValueError:
            return
        if value < 0 or value > 0o7777:
            return
        self._load_mode(value)
        # Restore the octal text (load_mode calls setText with a 4-char string
        # but if the user typed a partial value we should not overwrite them
        # mid-entry; only the symbolic label needs updating).
        self._updating = True
        try:
            self._octal_edit.setText(text)   # keep what the user typed
            self._sym_lbl.setText(mode_to_symbolic(value))
        finally:
            self._updating = False

    # ── Apply / Cancel ─────────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path


# ── Custom octal-only validator ────────────────────────────────────────────────

class _OctalValidator(QIntValidator):
    """Accept strings that are valid partial or complete octal numbers."""

    def validate(self, text: str, pos: int):
        from PySide6.QtGui import QValidator
        if text == "":
            return QValidator.State.Intermediate, text, pos
        for ch in text:
            if ch not in "01234567":
                return QValidator.State.Invalid, text, pos
        if len(text) > 4:
            return QValidator.State.Invalid, text, pos
        return QValidator.State.Acceptable, text, pos
