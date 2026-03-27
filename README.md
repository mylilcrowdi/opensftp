# openSFTP

**A dual-pane SFTP client for macOS, Linux, and Windows.**
Built with Python and PySide6. Native feel. No Electron.

[![CI](https://github.com/mylilcrowdi/opensftp/actions/workflows/ci.yml/badge.svg)](https://github.com/mylilcrowdi/opensftp/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-1982-brightgreen.svg)]()
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

![openSFTP main window](screenshots/01_main_window_idle.png)

---

## Screenshots

| Main window (dark) | Light theme | Connection dialog |
|---|---|---|
| ![idle](screenshots/01_main_window_idle.png) | ![light](screenshots/02_main_window_light.png) | ![connections](screenshots/03_main_window_connections.png) |

| Transfer panel — active | Transfer panel — paused | Remote panel |
|---|---|---|
| ![transfer active](screenshots/11_transfer_panel_active.png) | ![transfer paused](screenshots/12_transfer_panel_paused.png) | ![remote](screenshots/08_remote_panel_populated.png) |

---

## Features

**File management**
- Dual-pane layout: local filesystem left, remote right
- Drag-and-drop upload: drop files or folders onto the remote panel
- Cross-session drag & drop: move files between servers by dragging across tabs
- File permissions editor: chmod with checkboxes and octal input
- Remote search: filter by name or glob, results update live
- Bookmarks bar: pin favorite connections as quick-connect chips
- Edit remote files locally: opens in your editor, auto-uploads on save

**Transfers**
- 4 concurrent workers with auto-retry and exponential back-off
- Pause and resume individual jobs or the entire queue
- Resumable uploads and downloads: picks up after a dropped connection
- Per-file progress with accurate totals pre-calculated before the first byte moves
- Transfer history with search and statistics

**Connections**
- SSH key auth (Ed25519, RSA, ECDSA) and password auth
- SSH agent forwarding
- SSH config import: reads `~/.ssh/config` hosts automatically
- Test connection button in the connection dialog
- Keepalive interval configurable per connection
- SSH tunnel / jump host support
- Connections stored in `~/.config/sftp-ui/connections.json`
- Passwords optionally stored in the system keychain (macOS Keychain, libsecret, Windows Credential Manager)
- Auto-reconnect on disconnect

**Sessions**
- Multi-tab connections: open several servers in one window
- Session sidebar (Ctrl+Shift+S): visual panel with status dots and transfer badges
- Session restore: reopens tabs, paths, column widths, and sort order on every launch
- Cross-session file transfers: download from one server, upload to another

**Sync**
- Directory comparison: local only, newer, same, older, remote only
- Selective sync: choose exactly which entries to upload or download
- Conflict detection with configurable mtime tolerance

**Cloud storage**
- Amazon S3, MinIO, Backblaze B2, DigitalOcean Spaces (via `boto3`)
- Google Cloud Storage (via `google-cloud-storage`)
- Same dual-pane UI, no separate workflow

**UI**
- 6 themes: Dark, Light, Nord, Dracula, Solarized Dark, Frost (glassmorphism): switchable live
- System theme detection (follows macOS / GNOME dark mode automatically)
- Command palette (Ctrl+P): fuzzy search for any action
- Keyboard shortcuts overlay (F1 or Ctrl+?)
- Shimmer skeleton while directory listings load
- Animated status bar with transition effects

**Pro features**
- Up to 16 concurrent tabs (free: 3)
- Team site profiles: export/import connection sets
- Pro themes and advanced sync profiles
- Priority support

---

## Quick start

Clone and run with a single command. Python 3.11+ required.

```bash
git clone https://github.com/mylilcrowdi/opensftp.git
cd opensftp
python3 run.py
```

`run.py` creates the virtual environment, installs dependencies, and launches the app automatically. Works on macOS, Linux, and Windows.

```bash
python3 run.py          # Launch the app
python3 run.py --test   # Run the test suite
python3 run.py --setup  # Only create venv + install deps
python3 run.py --clean  # Remove the virtual environment
```

### Linux: Qt platform dependencies

PySide6 on Linux needs a few system libraries. Install them once:

```bash
# Ubuntu / Debian
sudo apt-get install -y \
  libgl1-mesa-dev libglib2.0-0 libdbus-1-3 \
  libxkbcommon0 libfontconfig1 libegl1 \
  libxcb-xinerama0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-xfixes0

# Fedora / RHEL
sudo dnf install -y \
  mesa-libGL glib2 dbus-libs \
  libxkbcommon fontconfig libglvnd-egl
```

<details>
<summary>Manual setup (without run.py)</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
PYTHONPATH=src python -m sftp_ui.app
```

</details>

---

## Build a native executable

[Briefcase](https://briefcase.readthedocs.io/) packages the app into a native installer — `.app` + `.dmg` on macOS, `.AppImage` on Linux, `.msi` on Windows. No Python required on the target machine.

```bash
pip install briefcase
```

**macOS**

```bash
briefcase create macOS
briefcase build macOS
briefcase package macOS    # → dist/*.dmg
```

**Linux**

```bash
briefcase create linux
briefcase build linux
briefcase package linux    # → dist/*.AppImage
```

**Windows**

```bash
briefcase create windows
briefcase build windows
briefcase package windows  # → dist/*.msi
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

**1982 tests across 68 test files.** Runs fully headlessly, no display required. Covers every module: transfer engine, queue, SFTP client, connection store, all UI panels, widgets, animations, sync logic, cloud clients, licensing, command palette, and sessions.

```
tests/
├── test_transfer.py / test_download.py         # upload + download engine, resume, retry
├── test_queue.py / test_queue_extended.py      # concurrent worker pool, pause/resume, cancel
├── test_sftp_client_integration.py             # in-process paramiko server, no Docker
├── test_load_pkey.py                           # SSH key loading: RSA, ECDSA, encrypted
├── test_connection.py / test_connection_dialog.py / test_connection_bugs.py
├── test_connection_test_button.py              # Test Connection feature
├── test_ssh_config_importer.py                 # SSH config import
├── test_sync_scan.py / test_sync_dialog.py / test_sync_model.py
├── test_remote_panel_ops.py / test_remote_model.py / test_remote_filter.py
├── test_local_panel.py / test_local_panel_ops.py / test_local_panel_keys.py
├── test_feature_drag_drop.py / test_remote_to_remote_dnd.py
├── test_feature_remote_search.py / test_feature_edit_remote.py
├── test_feature_auto_reconnect.py / test_feature_tabs.py
├── test_session_widget.py / test_signal_thread_safety.py
├── test_ssh_agent.py / test_keychain.py / test_tunnel.py
├── test_keepalive_tuning.py / test_permissions_dialog.py / test_bookmarks_bar.py
├── test_theme_manager.py / test_theme_dialog.py / test_frost_hires.py
├── test_cloud_client.py / test_cloud_connection_dialog.py
├── test_command_palette.py / test_license.py / test_license_dialog.py
├── test_animated_status_bar.py / test_skeleton_widget.py / test_transitions.py
├── test_transfer_history.py / test_transfer_panel.py / test_transfer_bar.py
├── test_ui_state.py / test_ui.py / test_main_window.py
└── test_shortcuts_dialog.py / test_platform_utils.py / test_sort_persistence.py
```

Run a single file during development:

```bash
pytest tests/test_transfer.py -v
```

---

## Project structure

```
src/sftp_ui/
├── app.py             # Entry point, QApplication setup
├── core/              # Zero-Qt business logic
│   ├── sftp_client.py       # paramiko wrapper, one connection per operation
│   ├── transfer.py          # Upload engine, chunked, resumable, retry
│   ├── queue.py             # Worker pool, N concurrent jobs, pause/resume/cancel
│   ├── connection.py        # Connection dataclass + JSON store
│   ├── connection_tester.py # Async connection test
│   ├── ssh_config_importer.py # ~/.ssh/config reader
│   ├── ui_state.py          # Session persistence (tabs, paths, column widths)
│   ├── license.py           # Pro license validation + activation
│   ├── transfer_history.py  # Transfer log with search
│   ├── team_profiles.py     # Export/import connection sets
│   ├── cloud_client.py      # S3 / GCS adapter
│   └── command_registry.py  # Command palette action registry
├── styling/
│   ├── themes/              # Hot-swappable QSS themes
│   │   ├── dark.qss / light.qss / nord.qss
│   │   ├── dracula.qss / solarized_dark.qss / frost.qss
│   └── theme_manager.py
├── animations/              # Named transition presets (fade, slide, pulse)
└── ui/
    ├── main_window.py       # Orchestrator, thread-safe signal bridge
    ├── session_widget.py    # Per-tab session: owns SFTPClient + TransferQueue
    ├── panels/
    │   ├── local_panel.py   # QTreeWidget, local filesystem
    │   └── remote_panel.py  # QTableView, remote filesystem + cross-session DnD
    ├── dialogs/
    │   ├── connection_dialog.py  # New/edit connection form
    │   ├── command_palette.py    # Fuzzy search command launcher (Ctrl+P)
    │   ├── license_dialog.py     # Pro license activation
    │   ├── sync_dialog.py        # Sync preview + job builder
    │   ├── permissions_dialog.py # chmod UI
    │   ├── theme_dialog.py       # Theme switcher
    │   └── shortcuts_dialog.py   # Keyboard shortcut reference (F1)
    └── widgets/
        ├── transfer_panel.py     # Live queue UI
        ├── session_sidebar.py    # Session list with status dots
        ├── bookmarks_bar.py      # Quick-connect chips
        ├── pro_gate.py           # Pro feature upgrade prompt
        ├── status_dot.py         # Connection state indicator
        ├── skeleton_widget.py    # Shimmer loading placeholder
        └── smooth_progress_bar.py
```

`core/` has zero Qt imports, every module is unit-testable without a display.

---

## Architecture

| Decision | Reason |
|---|---|
| PySide6 over PyQt6 | LGPL license, distributable without GPL contamination |
| One paramiko connection per SFTP operation | paramiko is not thread-safe; new connections are cheap |
| SessionWidget owns its own SFTPClient + queue | Tabs are fully independent; cross-session transfers go through temp files |
| Signal bridge for thread safety | Background threads emit signals, Qt main thread processes them |
| Jobs pre-registered before workers start | Accurate batch progress from tick one |
| Two-phase upload (local walk first) | Panel appears in <100 ms; remote round-trips run in background |
| `core/` with zero Qt imports | Business logic is fully unit-testable without a display |
| Briefcase for packaging | Native installers on all three platforms from one codebase |

---

## Contributing

Issues and PRs are welcome.

- Run `pytest tests/ --ignore=tests/test_e2e_screenshots.py -q` before opening a PR
- `core/` must stay Qt-free — no `PySide6` imports
- New features need tests
- CI runs on macOS and Linux (Python 3.11 + 3.13)

---

## License

The source code is MIT licensed — see [LICENSE](LICENSE).

MIT means you can use, modify, and distribute the code freely, including for commercial purposes.
You can build and run openSFTP yourself at no cost.

**Pro upgrade:** A license key unlocking additional tabs (16 vs 3), team profiles, and priority support
is available at [renemurrell.de/software/opensftp](https://renemurrell.de/software/opensftp) for $9 once.
Packaged desktop installers (no Python required) for macOS, Linux, and Windows are also available there.
