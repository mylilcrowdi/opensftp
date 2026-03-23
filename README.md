# SFTP UI

A modern, cross-platform SFTP client built with Python and PySide6.

Dual-pane interface — local filesystem on the left, remote server on the right.
Drag files across, track transfers in real time, compare directories before syncing.

![CI](https://github.com/mylilcrowdi/sftp-ui/actions/workflows/ci.yml/badge.svg)

---

## Features

- **Multi-connection manager** — SSH key (Ed25519 / RSA / ECDSA) and password auth; connections stored in `~/.config/sftp-ui/connections.json`
- **Parallel transfers** — 4 concurrent workers, auto-retry with back-off, resume interrupted transfers
- **Drag-and-drop upload** — drop files or folders onto the remote panel; animated overlay confirms the target
- **Sync preview** — compare a local and a remote directory side by side; filter by status (local only, newer, same, older, remote only); upload or download selected entries
- **Animated UI** — shimmer skeleton while listing loads, smooth progress bar, connection-state indicator dot
- **Themes** — dark (Catppuccin Mocha) and light, switchable at runtime
- **Session restore** — remembers last local and remote paths, reconnects automatically on next launch

---

## Requirements

- **macOS** 12 Monterey or later
- **Linux** — any modern distro with Qt5/Qt6 platform libraries (Ubuntu 22.04+, Fedora 37+, etc.)
- **Windows** 10 / 11 (64-bit)
- Python 3.11+

---

## Run from source

```bash
# 1. Clone
git clone https://github.com/mylilcrowdi/sftp-ui.git
cd sftp-ui

# 2. Create a virtual environment and install deps
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows (PowerShell)
pip install -r requirements.txt

# 3. Run
PYTHONPATH=src python -m sftp_ui.app
# Windows PowerShell:
# $env:PYTHONPATH="src"; python -m sftp_ui.app
```

### Linux: Qt platform dependencies

```bash
# Ubuntu / Debian
sudo apt-get install -y libgl1-mesa-dev libglib2.0-0 libxkbcommon0 libegl1

# Fedora / RHEL
sudo dnf install -y mesa-libGL glib2 libxkbcommon
```

---

## Running tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The test suite has **900+ tests** and runs fully headlessly (no display required).
It covers every module: transfer engine, queue, SFTP client, connection store,
all UI panels (local, remote, sync), widgets, and animations.

```
tests/
├── test_transfer.py               # upload engine — resume, retry, cancel
├── test_download.py               # download engine — resume, binary integrity
├── test_queue.py / _extended.py   # concurrent worker queue, generation cancel
├── test_sftp_client_integration.py   # in-process paramiko server, no Docker
├── test_load_pkey.py              # SSH key loading — RSA, ECDSA, encrypted
├── test_connection.py             # Connection dataclass + ConnectionStore
├── test_sync_scan.py              # sync comparison algorithm
├── test_sync_dialog.py            # _SyncModel display, job builders
├── test_remote_panel_ops.py       # RemotePanel state transitions
├── test_remote_model.py           # RemoteModel sort, display, icons
├── test_local_panel_*.py          # LocalPanel operations and keyboard shortcuts
├── test_ui_state.py               # session persistence (UIState)
├── test_theme_manager.py          # dark/light theme switching
├── test_animated_status_bar.py    # sweep animation, fade label
├── test_skeleton_widget.py        # shimmer animation lifecycle
├── test_transitions.py            # animation presets (fade, slide, pulse)
└── test_transfer_panel.py         # TransferPanel widget state
```

---

## Project structure

```
src/sftp_ui/
├── core/          # Zero-Qt business logic — SFTP client, transfer engine, queue, persistence
├── styling/       # Hot-swappable QSS themes (dark.qss, light.qss)
├── animations/    # Named transition presets (fade_in, fade_out, …)
└── ui/
    ├── main_window.py     # Orchestrator; thread-safe signal bridge
    ├── panels/            # LocalPanel (QTreeWidget), RemotePanel (QTableView)
    ├── dialogs/           # Connection form, Sync preview
    └── widgets/           # TransferPanel, StatusDot, SkeletonWidget, SmoothProgressBar
```

`core/` has zero Qt imports and is fully unit-testable without a display.

---

## Architecture

| Decision | Reason |
|---|---|
| PySide6 over PyQt6 | LGPL license — no GPL contamination |
| Each SFTP op gets its own connection | paramiko is not thread-safe |
| Jobs pre-registered before workers start | Accurate batch progress totals from tick one |
| Two-phase upload (local walk first) | Panel appears in <100 ms; remote round-trips happen in background |

---

## License

MIT — see [LICENSE](LICENSE).
