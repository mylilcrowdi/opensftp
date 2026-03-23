# SFTP UI — Improvement Log

---

## 2026-03-22 — UI polish round 10: sync columns, mtime tolerance, system theme, host:port split, shortcuts dialog

### Sync dialog — column widths: Interactive resize mode

**What:** All non-checkbox columns in the sync preview table (`Status`, `Path`,
`Local`, `Remote`, `Local modified`, `Remote modified`) now use
`QHeaderView.ResizeMode.Interactive` instead of `Stretch` / `ResizeToContents`.
Sensible pixel defaults are set for each column. `setStretchLastSection(False)`
ensures the last column is not forcibly stretched.

**Why:** `Stretch` on the Path column prevented the user from resizing any
column — the Path column silently consumed all available space. Columns set to
`ResizeToContents` also reject user drags. `Interactive` lets the user drag any
column header to their preferred width, matching the behaviour already fixed in
the remote panel in prior rounds.

**How:** Replaced the individual `setSectionResizeMode` calls with a loop over
all non-checkbox columns setting `Interactive`, then `setColumnWidth()` to set
explicit defaults.

**Files:** `src/sftp_ui/ui/dialogs/sync_dialog.py`

---

### Sync scan — mtime tolerance: ±2 s window for FAT filesystem rounding

**What:** When comparing local vs remote mtimes to decide whether sizes-differ
means `LOCAL_NEWER` or `REMOTE_NEWER`, the comparison now applies a **±2 second
tolerance window**. Files whose sizes differ but whose mtimes are within 2 s of
each other are classified as `SAME`.

**Why:** FAT and exFAT filesystems store file timestamps with 2-second
granularity. After an upload/download round-trip the mtime written by the SFTP
server can differ by up to 2 s from the local mtime even when the file content
is identical. A strict `lm > rm` comparison falsely flags every such file as
`LOCAL_NEWER` or `REMOTE_NEWER` on every subsequent sync, causing redundant
transfers.

**How:** Added `elif abs(lm - rm) <= 2.0: st = SyncStatus.SAME` between the
size-equality check and the directional mtime comparison. Added 4 tests covering
within-tolerance (SAME), boundary (exactly 2 s → SAME), and beyond-tolerance
(> 2 s → not SAME) cases.

**Files:** `src/sftp_ui/ui/dialogs/sync_dialog.py`, `tests/test_sync_scan.py`

---

### Theme manager — auto-detect OS dark/light preference on startup

**What:** On launch the app now detects the operating system's colour-scheme
preference (dark or light) via `QGuiApplication.styleHints().colorScheme()` and
applies the matching theme automatically. Previously it always started in dark
mode regardless of the OS setting.

**Why:** A user who has configured their OS to use a light theme expects apps
that respect system preferences to start in light mode. Forcing dark on every
launch is at best surprising and at worst inaccessible for users who rely on
high-contrast light themes.

**How:** Added a module-level `_system_prefers_dark()` helper in
`theme_manager.py` that reads `Qt.ColorScheme` via `QStyleHints` (Qt 6.5+),
falling back to `True` (dark) if the API is unavailable. Added a new
`apply_system_theme()` public method that calls `_system_prefers_dark()` and
then `self.apply()`. Changed `app.py` to call `theme_manager.apply_system_theme()`
instead of `theme_manager.apply("dark")`. Added 4 tests.

**Files:** `src/sftp_ui/styling/theme_manager.py`, `src/sftp_ui/app.py`,
`tests/test_theme_manager.py`

---

### ConnectionDialog — host:port auto-split when pasting

**What:** Pasting a `host:port` string (e.g. `example.com:2222`) into the Host
field now automatically splits the value: the hostname is placed in the Host
field and the port number is placed in the Port spinner. Plain hostnames without
a colon are left unchanged. IPv6 literals (`[::1]:22`) are excluded from
splitting (the guard checks for a leading `[`).

**Why:** Users often copy server addresses in `host:port` form from
documentation, SSH config, or terminal output. Without auto-splitting they must
manually separate the parts and change the port spinner, which is error-prone.

**How:** Added `_on_host_edited(text)` connected to `self._host.textEdited`
(fires only on user edits, not `setText`). The method uses `str.rpartition(":")`
to split, validates that the port part is a digit string with a value in
`1–65535`, then calls `blockSignals(True)` / `setText()` / `blockSignals(False)`
so the programmatic correction does not re-trigger the handler. Updated the Host
placeholder text to mention `host:port`. Added 6 tests.

**Files:** `src/sftp_ui/ui/dialogs/connection_dialog.py`,
`tests/test_connection_dialog.py`

---

### Keyboard shortcut cheatsheet — F1 / Ctrl+?

**What:** Pressing **F1** or **Ctrl+?** now opens a `QMessageBox` listing all
keyboard shortcuts available in the app. The dialog can be dismissed with Enter
or Escape.

**Why:** The app has accumulated shortcuts across many rounds (Ctrl+K, F5,
Ctrl+G, Ctrl+Shift+., Delete, F2, Ctrl+A, Ctrl+U, Ctrl+D, Backspace…) with no
discoverable reference. Users who prefer the keyboard had no way to learn these
without reading source code.

**How:** Added `_SHORTCUTS_TEXT` class attribute (HTML table) and a
`_show_shortcuts_dialog()` method to `MainWindow`. Wired two new `QShortcut`
instances — `F1` and `Ctrl+?` — to the method in `_connect_signals()`.

**Files:** `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — UI polish round 9: thread safety, local dotfile toggle, upload permissions

### main_window.py — _on_connect thread safety: set_sftp dispatched via signal

**What:** `_remote_panel.set_sftp(sftp)` was being called directly from a
background thread inside `_do()`. `set_sftp` calls `self._empty_state.hide()`,
which is a `QWidget` method. Qt requires all widget operations to happen on the
main thread; calling them from a worker thread is undefined behaviour and can
cause silent corruption or crashes.

**Why:** PySide6 does not raise an exception when a QWidget method is called
from a non-main thread — the crash manifests later, non-deterministically. The
risk was present on every connect attempt.

**How:** Added a `set_sftp = Signal(object)` to `_Signals` and wired it to
`self._remote_panel.set_sftp` in `_connect_signals()`. The background thread
now calls `self._signals.set_sftp.emit(sftp)` instead of calling `set_sftp`
directly. Qt's cross-thread signal delivery ensures the slot runs on the main
thread.

**Files:** `src/sftp_ui/ui/main_window.py`

---

### Local panel: show/hide dotfiles toggle (.*) button

**What:** The local file tree now has a `.*` toggle button in its header row,
matching the equivalent button on the remote panel. When toggled off (default)
dotfiles and dotdirectories are hidden. Toggling it on reveals them. The button
is checkable and persists its state for the current session.

**Why:** The remote panel has had a dotfile toggle since the beginning; the
local panel never got one. Without it, dotfiles like `.env`, `.gitignore`,
`.ssh` etc. were always visible in the local tree even when the remote tree was
filtering them — an inconsistent and cluttered experience.

**How:** Added `_show_hidden: bool = False` to `LocalPanel.__init__`. Changed
`_build_ui` to use a `QHBoxLayout` header row containing both the "Local"
`QLabel` and a new `QPushButton(".*")` (checkable, `hidden-toggle` object
name, `setFixedWidth(32)`), wired to a new `_on_hidden_toggled(checked)` slot.
In `_populate()`, added a `if not self._show_hidden and entry.name.startswith("."):`
filter before adding each item. Added `QHBoxLayout` and `QPushButton` to
imports. Added 6 tests in `tests/test_local_panel.py`.

**Files:** `src/sftp_ui/ui/panels/local_panel.py`, `tests/test_local_panel.py`

---

### Upload: preserve local file permissions on remote copy

**What:** After a successful upload, the remote file's permissions are now set
to match the local file's permission bits via `sftp.chmod()`. Previously every
uploaded file was created with the server's default umask (typically `0644`),
regardless of whether the local file was executable (`0755`) or privately owned
(`0600`).

**Why:** Uploading a shell script (`chmod +x deploy.sh`) and discovering it is
no longer executable on the server is a common and frustrating workflow break.
The same applies to private key files or config files with restricted
permissions.

**How:** Added `SFTPClient.chmod(remote_path, mode)` method wrapping
`paramiko.SFTPClient.chmod`. At the end of `TransferEngine.upload()`, after the
job state is set to `DONE`, a `stat.S_IMODE(os.stat(local_path).st_mode)` call
reads the local permission bits and passes them to `self._sftp.chmod()`. Errors
are swallowed so a permission-denied response from a restricted server never
turns a successful transfer into a failure. The skip path (remote already
complete, no bytes transferred) intentionally does not re-chmod. Added `chmod`
stub to `FakeSFTPClient` in `conftest.py`. Added 3 tests in
`tests/test_transfer.py`.

**Files:** `src/sftp_ui/core/sftp_client.py`, `src/sftp_ui/core/transfer.py`,
`tests/conftest.py`, `tests/test_transfer.py`

---

## 2026-03-22 — UI polish round 8: password visibility, local sort, browse memory

### ConnectionDialog: show/hide toggle on password fields

**What:** Every password-type field in the Connection dialog (Key Passphrase,
Password, Tunnel Key Passphrase, Tunnel Password) now has a small eye-button (👁)
next to it. Clicking toggles the field between `EchoMode.Password` (dots) and
`EchoMode.Normal` (clear text). The button is tab-focusable.

**Why:** Users cannot verify a passphrase they just typed without this toggle.
Typing errors in a masked field are invisible, which is especially frustrating
for SSH passphrases that contain symbols or have specific capitalisation.

**How:** Added a module-level `_make_password_row(line_edit)` helper that wraps
any `QLineEdit` in an `QHBoxLayout` with a `QPushButton("👁")` toggle. Replaced
the four bare `form.addRow("...", self._xxx)` calls for password fields with
`form.addRow("...", _make_password_row(self._xxx))`. The toggle button connects
`toggled` to `setEchoMode`.

**Files:** `src/sftp_ui/ui/dialogs/connection_dialog.py`

---

### SSH key Browse button: remember last-used directory

**What:** The "Browse…" button for the SSH Key field (and the Tunnel Key field)
now opens the last directory the user browsed to instead of always defaulting to
`~/.ssh`. The directory is persisted across sessions via `QSettings`.

**Why:** Users who store keys outside `~/.ssh` (e.g. `~/keys/`, an external
drive, a project directory) had to navigate away from `.ssh` on every use.

**How:** Both `_browse_key()` and `_browse_tunnel_key()` now read
`QSettings("sftp-ui", "sftp-ui")` for the key `"browse/last_key_dir"`, falling
back to `~/.ssh` on first use. After a successful selection the parent directory
is written back to the same key. Both methods share the same settings key so
browsing from either field updates the remembered directory for both.

**Files:** `src/sftp_ui/ui/dialogs/connection_dialog.py`

---

### Local panel: sort by Name, Size, and Modified

**What:** The local file tree now shows three columns — Name, Size, and Modified
— and supports column-header click sorting. Clicking a column header cycles
through ascending → descending → neutral (natural dirs-first order). The sort
indicator on the header updates accordingly.

**Why:** The remote panel already had three-column sorting; the local panel
showed only a Name column with no sort control. Users had no way to find the
most recently modified file or identify large files on the local side.

**How:** Changed `setHeaderLabel("Name")` to `setHeaderLabels(["Name", "Size",
"Modified"])` and set initial column widths. Connected `header().sectionClicked`
to a new `_on_header_click()` method that cycles sort state (mirroring
`RemotePanel._on_header_click`). Items are populated with human-readable size
and ISO-date strings. Sorting is applied via `setSortingEnabled(True) →
sortByColumn() → setSortingEnabled(False)` to avoid triggering an automatic
re-sort on `addTopLevelItem`. The `..` entry always sorts first because `..`
lexicographically precedes all filename characters.

**Files:** `src/sftp_ui/ui/panels/local_panel.py`

---

### Feature not implemented: recent remote paths

**What:** No "Recents" menu or breadcrumb history for remote paths is
implemented. This is a future feature.

**Why:** Implementing recents requires tracking navigation history per connection
and persisting it via `QSettings`. The mechanism would also need to handle
servers being deleted or unreachable. This is non-trivial and is deferred to
avoid scope creep in this round.

**Future work:** Track the last N remote paths per connection ID in
`QSettings("sftp-ui", "recents")`. Surface them as a drop-down on the
breadcrumb bar or a "Recent Paths" submenu.

---

## 2026-03-22 — UI polish round 7: edge cases and keyboard navigation

### Remote panel: sort state reset on directory navigation

**What:** The sort indicator and internal `_sort_col` / `_sort_order` state are
now reset to "neutral" whenever a new directory listing completes. Previously the
sort arrow from directory A persisted visually and semantically after navigating
to directory B.

**Why:** A stale sort indicator from a previous directory is misleading — the
user sees "sorted by Name ↑" but the current listing is in server order. The
sort state should belong to the current directory, not survive navigation.

**How:** Added three lines at the top of `_on_listdir_done()` that reset
`_sort_col = -1`, `_sort_order = AscendingOrder`, and call
`horizontalHeader().setSortIndicator(-1, …)`. Updated the corresponding test in
`test_remote_panel_ops.py` from `test_sort_preserved_after_listdir` to
`test_sort_reset_after_listdir` asserting the new neutral-on-navigate behaviour.

**Files:** `src/sftp_ui/ui/panels/remote_panel.py`, `tests/test_remote_panel_ops.py`

---

### Status bar: informational messages auto-clear after 5 seconds

**What:** Purely informational status bar messages (directory summaries,
"Done — file transferred", etc.) now disappear after 5 seconds, leaving the
bar blank. Error-type messages ("failed", "error", "denied", "cancelled",
"disconnect") and in-progress messages (containing "…") are not auto-cleared.

**Why:** A message like "Loaded 42 items" from five minutes ago is stale and
misleading. The status bar should reflect the current state, not linger with
outdated information.

**How:** Added a `QTimer` (`_clear_timer`, single-shot, 5 000 ms) to
`AnimatedStatusBar`. In `showMessage()`, the timer is started for informational
messages and stopped for in-progress or error messages. Added `QTimer` to the
import line.

**Files:** `src/sftp_ui/ui/widgets/animated_status_bar.py`

---

### Error recovery after failed connect — full UI reset

**What:** When a connection attempt fails (`_on_connect_failed`), the UI is now
fully reset to "disconnected" state: `_active_conn` and `_sftp` are set to
`None`, Disconnect/Refresh/Sync buttons are disabled, and the window title
reverts to "SFTP UI".

**Why:** Previously only the Connect button was re-enabled and the status dot
was set to the failure state. `_active_conn` remained set to the failed
connection, so subsequent operations that guard on `_active_conn` (Sync, etc.)
could proceed incorrectly after a failed connect. The disconnect button also
remained enabled, creating a confusing state.

**How:** Added explicit resets for `_active_conn = None`, `_sftp = None`,
`_disconnect_btn.setEnabled(False)`, `_refresh_btn.setEnabled(False)`,
`_sync_btn.setEnabled(False)`, and `self.setWindowTitle("SFTP UI")` in
`_on_connect_failed()`.

**Files:** `src/sftp_ui/ui/main_window.py`

---

### Theme QSS: focus ring on QPushButton for keyboard navigation

**What:** Focused buttons now show a visible coloured border (matching the
input-focus colour: blue `#89b4fa` in dark theme, `#1e66f5` in light) when
navigated to with Tab or arrow keys.

**Why:** `QLineEdit`, `QSpinBox`, and `QComboBox` already had `:focus` rules,
but `QPushButton` did not. Without a focus ring, Tab-navigating through toolbar
buttons gives no indication of which button is focused — important for
keyboard-only and accessibility workflows.

**How:** Added `QPushButton:focus { border-color: …; outline: none; }` to both
`dark.qss` and `light.qss`, immediately after the existing `:pressed` rule.

**Files:** `src/sftp_ui/styling/themes/dark.qss`, `src/sftp_ui/styling/themes/light.qss`

---

### ConnectionDialog: group field autocomplete from existing connections

**What:** The Group field in the connection dialog now offers inline autocomplete
suggestions drawn from the group names already used by other connections. Typing
"Pr" in a store that has a "Production" group will suggest "Production".

**Why:** Without autocomplete, users must remember (and type consistently) every
group name. A single typo creates a duplicate group ("Prodution" vs
"Production"), splitting the connection list unexpectedly.

**How:** Added an optional `store: ConnectionStore` parameter to
`ConnectionDialog.__init__`. A new `_install_group_completer()` method reads
unique non-empty group names from the store, creates a `QCompleter` in
`InlineCompletion` mode, and installs it on `_group`. `MainWindow` now passes
`store=self._store` when opening New and Edit dialogs.

**Files:** `src/sftp_ui/ui/dialogs/connection_dialog.py`, `src/sftp_ui/ui/main_window.py`

---

### Sync scan: guard against symlink loops in remote BFS walk

**What:** The parallel remote directory walk in `_walk_remote_parallel` now
skips entries where `is_symlink=True and is_dir=True`, and tracks all visited
paths to guard against duplicate enqueuing.

**Why:** A symlinked directory on the server (e.g. `/data/logs → /data`)
would previously be enqueued as a subdirectory, causing the BFS to loop
indefinitely — stalling the sync scan and eventually exhausting memory.
`Path.rglob` on the local side already avoids this via `recurse_symlinks=False`
(Python 3.13 default), but the remote BFS had no equivalent guard.

**How:** Added a `visited: set[str]` initialised with the root path. In the
worker's entry loop, directory symlinks (`e.is_symlink`) are skipped entirely;
non-symlink directories are only enqueued if their path is not already in
`visited`. The `visited` set is checked and updated under the existing `lock`.

**Files:** `src/sftp_ui/ui/dialogs/sync_dialog.py`

---

## 2026-03-22 — UI polish round 6: robustness and edge cases

### Concurrent navigation race — stale result suppression

**What:** `_on_listdir_done` and `_on_listdir_error` now check the generation
counter before applying results. Previously only `_on_listdir_progress` checked
the generation, so a slow in-flight listdir for a directory the user had already
navigated away from could overwrite `_cwd` and the model with stale data.

**Why:** Rapid folder clicking (especially on high-latency connections) could make
the panel show the contents of a directory that is no longer the current one.

**How:** Added `gen` as a third argument to the `done` and `error` signals in
`_ListdirSignals`. Both handlers (`_on_listdir_done`, `_on_listdir_error`) now
return immediately when `gen != self._nav_gen`. Tests updated to pass a matching
gen value when calling handlers directly.

**Files:** `src/sftp_ui/ui/panels/remote_panel.py`, `tests/test_remote_panel_ops.py`

---

### Breadcrumb overflow — horizontal scroll for deep paths

**What:** The breadcrumb bar now wraps its crumb buttons in a `QScrollArea`
(horizontal scrolling, no visible scrollbar). After every path rebuild it
auto-scrolls to the rightmost segment so the current directory is always visible
regardless of depth.

**Why:** A path like `/a/b/c/d/e/f/g/h/i/j` previously caused the breadcrumb
buttons to overflow the panel width and be clipped.

**How:** Wrapped `_crumb_widget` in a `QScrollArea` with hidden scrollbars and
`QSizePolicy.Fixed` height. `focus_editor` / `_on_confirm` / `Escape` handler
were updated to hide/show `_crumb_scroll` instead of `_crumb_widget`. A
`QTimer.singleShot(0)` scrolls to `horizontalScrollBar().maximum()` after each
rebuild so the deepest segment is always visible.

**Files:** `src/sftp_ui/ui/panels/remote_panel.py`, `tests/test_ui.py`

---

### Download overwrite prompt — consistent with upload behaviour

**What:** Downloading a file when a local copy already exists now shows the same
Overwrite / Skip / Cancel dialog that uploads show. Previously downloads
silently overwarded or resumed with no user prompt.

**Why:** Upload has an overwrite dialog (from round 3); download did not. The
inconsistency was surprising — the same file could be silently replaced on
download but prompted on upload.

**How:** Added a post-expansion overwrite-classification step in
`_on_download_requested` (in `main_window.py`) that mirrors the upload logic:
files are classified as "unchanged" (same local/remote size), "conflict"
(different size), or "new". When conflicts exist the existing
`_ask_overwrite` / `show_overwrite_dialog` mechanism is reused. Jobs that are
skipped or cancelled are never enqueued.

**Files:** `src/sftp_ui/ui/main_window.py`

---

### Disconnect during active transfer — confirmation guard

**What:** Clicking Disconnect while file transfers are in progress now shows a
confirmation dialog: "N transfers are still in progress. Disconnecting will
cancel them. Continue?" with No as the default button.

**Why:** Previously, clicking Disconnect mid-transfer silently killed all
running jobs with no warning, making it easy to accidentally corrupt partial
downloads/uploads.

**How:** Added a `self._queue.pending_count() > 0` guard at the top of
`_on_disconnect()` in `main_window.py` that shows `QMessageBox.question` before
proceeding. The dialog defaults to "No" so pressing Enter does not accidentally
disconnect.

**Files:** `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Remote panel: multi-file selection feedback in status bar

**What:** The remote panel now updates the status bar whenever the table selection
changes. Selecting a single file shows its name and size; selecting multiple items
shows "N items selected (X folders, Y files)"; deselecting all restores the
directory summary.

**Why:** With ExtendedSelection enabled, users can select dozens of files before
downloading. Without feedback there was no way to confirm how many items were
selected without counting manually.

**How:** Connected `self._table.selectionModel().selectionChanged` to a new
`_on_selection_changed()` slot in `RemotePanel._build_ui()`. The slot reads the
current selection, filters out the `..` navigation entry, and emits an appropriate
`status_message`.

**Files:**
- `src/sftp_ui/ui/panels/remote_panel.py`

---

## 2026-03-22 — ConnectionDialog: tunnel fields highlighted on empty save

**What:** When the SSH Tunnel section is enabled and the user clicks Save with
Tunnel Host or Tunnel User left blank, those fields now receive a red border and
an error message is displayed — matching the behaviour for the main connection's
required fields.

**Why:** The tunnel hostname and username are genuinely required when tunnelling
is enabled. Previously the `TunnelConfig` dataclass would raise `ValueError`
with a bare string that appeared in the error label, but the empty fields were
not highlighted, making the error hard to locate.

**How:** Added explicit empty-field checks for `_tunnel_host` and `_tunnel_user`
in `ConnectionDialog._on_accept()` when the tunnel checkbox is checked. Uses the
existing `_set_field_error()` helper for consistent styling.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`

---

## 2026-03-22 — ConnectionDialog: SSH key path existence check

**What:** If the user manually types an SSH key path (rather than using Browse)
that does not exist on disk, saving the dialog now shows an error and highlights
the key field in red. The same check applies to the tunnel key path.

**Why:** A mistyped or stale key path would silently save and only fail at
connect time with a cryptic I/O error. Catching it at save time gives an
immediate, actionable message.

**How:** Added `Path(key_text).exists()` checks for both `_key_path` and
`_tunnel_key_path` in `ConnectionDialog._on_accept()` before constructing the
`Connection` object. Paths that are empty (no key set) are skipped.
Updated 3 existing tests that used the non-existent placeholder `/some/key` to
use the `tmp_key` fixture which creates a real temporary file.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`
- `tests/test_connection_dialog.py`
- `tests/test_tunnel.py`

---

## 2026-03-22 — Main window: save and restore window geometry

**What:** The application now remembers its window size and position between
sessions using `QSettings`. On first launch (or after settings are cleared) the
default 1200 × 750 geometry is used; on subsequent launches the last-used
geometry is restored.

**Why:** Users who resize or reposition the window lose their layout on every
restart, which is frustrating when working on smaller or multi-monitor setups.

**How:** Added `_restore_geometry()` (called at the end of `__init__`) and
`_save_geometry()` (called at the start of `closeEvent`) to `MainWindow`.
Both use `QSettings("sftp-ui", "sftp-ui")` with the key `"window/geometry"`.
`QByteArray` is imported from `PySide6.QtCore` for the type annotation.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Local panel: file count in status bar on directory load

**What:** When the local panel navigates to a new directory, the status bar now
shows a summary of the form "3 folders, 12 files" — matching the existing
behaviour of the remote panel.

**Why:** The remote panel has always emitted a directory summary after a listing
completes. The local panel had no equivalent, leaving the status bar showing
whatever the remote panel last emitted, which was confusing when the user was
working on the local side.

**How:** Added a `status_message = Signal(str)` to `LocalPanel` and modified
`_populate()` to count directories and files while building the tree, then emit
the summary at the end. Wired the new signal to `self._status.showMessage` in
`MainWindow._connect_signals()`.

**Files:**
- `src/sftp_ui/ui/panels/local_panel.py`
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — QSpinBox / QComboBox: prevent accidental scroll-wheel changes

**What:** Set `FocusPolicy.StrongFocus` on all `QSpinBox` widgets (port fields in
`ConnectionDialog` — both the main connection port and the tunnel port) and on the
`QComboBox` connection selector in the toolbar.

**Why:** Qt's default `WheelFocus` policy makes spinboxes and comboboxes consume
scroll-wheel events even when they have not been explicitly clicked. A user scrolling
through the connection dialog form or the main window would unintentionally increment
the port number or cycle through connections. `StrongFocus` requires the widget to be
explicitly focused (clicked or Tab'd into) before it captures wheel events.

**How:** Added `self._port.setFocusPolicy(Qt.FocusPolicy.StrongFocus)` and the same
for `self._tunnel_port` in `ConnectionDialog._build_ui()`. Added the same on
`self._conn_combo` in `MainWindow._build_toolbar()`.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — ConnectionDialog: auto-focus Name field on open

**What:** The Name `QLineEdit` now receives keyboard focus immediately when the
dialog opens (both for new connections and when editing an existing one).

**Why:** The user has to click the Name field before typing. For "New Connection" it
is always the first thing entered; for "Edit Connection" it is the most common field
to change. Not having focus there forces an extra mouse click.

**How:** Added `self._name.setFocus()` at the end of `ConnectionDialog.__init__`,
after `_build_ui()` and the optional `_populate()` call.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`

---

## 2026-03-22 — Remote panel: enforce minimum column width (60 px)

**What:** Set `hdr.setMinimumSectionSize(60)` on the remote panel's
`QHeaderView`.

**Why:** The three table columns (Name, Size, Modified) could be dragged all the way
to 0 px, making them invisible and leaving no way to drag them back without resizing
the window. A 60 px floor is narrow enough to never feel restrictive but guarantees
the column header grip is always reachable.

**How:** Inserted `hdr.setMinimumSectionSize(60)` directly before the individual
`resizeSection` calls in `RemotePanel._build_ui()`.

**Files:**
- `src/sftp_ui/ui/panels/remote_panel.py`

---

## 2026-03-22 — Local panel: enforce minimum column width (60 px)

**What:** Added `QHeaderView` import and called
`self._tree.header().setMinimumSectionSize(60)` on the local panel's `QTreeWidget`
header.

**Why:** Same as the remote panel issue — the single "Name" column in the local file
tree could be dragged to zero width with no way to restore it.

**How:** Added `QHeaderView` to the import list and inserted
`self._tree.header().setMinimumSectionSize(60)` after `setHeaderLabel("Name")` in
`LocalPanel._build_ui()`.

**Files:**
- `src/sftp_ui/ui/panels/local_panel.py`

---

## 2026-03-22 — Remote panel: breadcrumb preserved on listdir error

**What:** When a directory listing fails, the breadcrumb bar is now restored to the
last valid path (`_cwd`) instead of being overwritten with the error message string.
The error is surfaced through the `status_message` signal (i.e. the status bar at the
bottom of the window) where it is readable without corrupting the navigation UI.

**Why:** The previous code set `self._breadcrumb.set_path(f"Error: {msg}")`, which
stored the error string as the panel's internal `_path`. Subsequent operations
(breadcrumb clicks, Ctrl+G, refresh) would use that corrupted string as a real path,
causing confusing follow-on errors. The status bar is the conventional place for
transient error feedback.

**How:** Replaced the single corrupted-path call with two lines:
`self._breadcrumb.set_path(self._cwd)` (rollback to last valid path) and
`self.status_message.emit(...)` (surface the error to the status bar).
Updated the corresponding test in `tests/test_remote_panel_ops.py` to assert the
correct new behaviour and added a second test verifying that the error text is emitted
as a status message.

**Files:**
- `src/sftp_ui/ui/panels/remote_panel.py`
- `tests/test_remote_panel_ops.py`

---

## 2026-03-22 — Local panel: fix file handle leak in New File

**What:** Changed `open(path, "w").close()` to a `with open(path, "w"): pass`
context-manager form in `LocalPanel._do_new_file()`.

**Why:** The bare `open(...).close()` pattern relies on CPython's reference-counting
garbage collector to close the file handle immediately. Under alternative Python
implementations (PyPy, Jython) or if the interpreter delays cleanup, the handle can
remain open briefly. Using a `with` block is explicit and deterministic across all
runtimes.

**How:** Replaced the one-liner with a two-line `with` block.

**Files:**
- `src/sftp_ui/ui/panels/local_panel.py`

---

## 2026-03-22 — ConnectionDialog: Save button is now the default (Enter submits)

**What:** The Save button in `ConnectionDialog` now has `setDefault(True)`, so pressing Enter
anywhere in the form (except inside a multiline widget) submits the connection.

**Why:** Without a default button, pressing Enter in the dialog did nothing — the user had
to reach for the mouse to click Save. This is a standard expectation for modal forms.

**How:** Added `save_btn.setDefault(True)` immediately after `save_btn.setObjectName("primary")`
in `ConnectionDialog._build_ui()`.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`

---

## 2026-03-22 — ConnectionDialog: dialog shrinks when SSH Tunnel section is collapsed

**What:** Collapsing the SSH Tunnel `QGroupBox` now calls `self.adjustSize()` so the dialog
height decreases back to its compact form. Previously, hiding the group box left a large
blank gap at the bottom of the dialog.

**Why:** `QGroupBox.setVisible(False)` removes the widget from layout flow but the dialog does
not automatically recalculate its preferred size. `adjustSize()` forces a geometry
recalculation so the window snaps to its minimum height.

**How:** Replaced the direct `toggled.connect(self._tunnel_group.setVisible)` binding with a
small lambda that calls both `setVisible` and `self.adjustSize()`.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`

---

## 2026-03-22 — Window title shows active connection name

**What:** The main window title updates to `"SFTP UI — <connection name>"` on successful
connect and resets to `"SFTP UI"` on disconnect.

**Why:** When multiple windows or SSH sessions are open, a static `"SFTP UI"` title gives no
context about which server is active. Showing the connection name in the title bar makes it
immediately identifiable in the taskbar / window switcher.

**How:** Added `self.setWindowTitle(f"SFTP UI — {self._active_conn.name}")` in
`_on_connect_success` (inside the `if self._active_conn` guard that already existed) and
`self.setWindowTitle("SFTP UI")` in `_on_disconnect`.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Toolbar combo: Enter/Return key triggers connect

**What:** Selecting a connection from the combo box via keyboard (arrow keys + Enter) now
immediately initiates a connection attempt without needing to click the Connect button.

**Why:** `QComboBox.activated` fires when the user confirms a selection in the dropdown. Wiring
it to `_on_connect` means keyboard-only workflows no longer require a mouse click.

**How:** Added `self._conn_combo.activated.connect(lambda _idx: self._on_connect())` at the
end of `_build_toolbar()`.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Status bar: suppress noise from intentional upload skips

**What:** The status bar no longer flashes `"Cancelled: <filename>"` for every file that was
intentionally skipped during an upload (up-to-date or skip-existing). Only genuine
user-initiated cancellations update the status bar.

**Why:** The upload engine cancels pre-registered jobs that are skipped (unchanged or the user
chose "Skip Existing"). These hit `_on_job_cancelled`, which was unconditionally writing
`"Cancelled: <filename>"` to the status bar — overwriting the informative summary message
and making the final state look like an error.

**How:** Added a guard `if not job.error:` in `_on_job_cancelled`. Intentional skips set
`job.error` to a human-readable reason (`"up to date"`, `"skipped (exists)"`); genuine
user cancellations leave it empty.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Connection Manager: Enter key triggers connect

**What:** Pressing Enter/Return while a connection is highlighted in the Connection Manager
list now triggers the Connect action (same as double-clicking the row).

**Why:** The list supports keyboard navigation (arrow keys to move selection) but had no Enter
binding to act on the selection. Users navigating by keyboard had to mouse-click Connect.

**How:** Installed an `eventFilter` on `self._list` in `ConnectionManagerDialog._build_ui()`.
The filter intercepts `Key_Return` / `Key_Enter` key-press events and calls `_on_connect()`.
Also added `QEvent` to the `PySide6.QtCore` import.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_manager.py`

---

## 2026-03-22 — Splitter: enforce minimum panel width (200 px)

**What:** Both the local panel and remote panel now have `setMinimumWidth(200)`, preventing
the splitter from collapsing either side to zero.

**Why:** Without a minimum width, dragging the splitter handle all the way to either edge
collapses one panel entirely, with no way to restore it without resizing the window.

**How:** Added `self._local_panel.setMinimumWidth(200)` and
`self._remote_panel.setMinimumWidth(200)` immediately after the splitter setup in
`MainWindow._build_ui()`.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Local panel path label: prevent vertical growth on narrow windows

**What:** The local panel path label no longer wraps and pushes the file tree down when
the window is narrow. Long paths are now elided in the middle with "…". A tooltip shows
the full path.

**Why:** `setWordWrap(True)` was previously set on the label. On a narrow window, a long
path like `/home/user/Documents/Projects/my-app/assets` would wrap to multiple lines,
compressing the tree widget unexpectedly. The layout should stay stable regardless of path
length.

**How:** Removed `setWordWrap(True)`; added `setMaximumHeight(24)` to cap the label to one
line. In `_populate()`, the path is elided via `fontMetrics().elidedText()` (ElideMiddle)
and the full path is stored in `setToolTip()`.

**Files:**
- `src/sftp_ui/ui/panels/local_panel.py`

---

## 2026-03-22 — Transfer panel queue label: exclude intentional skips from "failed" count

**What:** The queue toggle label (e.g. "▸ Queue · 5 jobs · 2 failed") no longer counts
intentional skips (up-to-date / skip-existing) as "failed".

**Why:** Skipped uploads are `CANCELLED` jobs with `job.error` set to a reason string.
The previous counter included all `CANCELLED` jobs in the "failed" count, so a 100-file
upload where 40 files were up-to-date would show "40 failed" — alarming and misleading.

**How:** Changed the `failed` counter in `_update_toggle_label()` to count only
`FAILED` state jobs and `CANCELLED` jobs where `job.error` is empty (genuine user
cancellations), matching the same logic used elsewhere in the codebase.

**Files:**
- `src/sftp_ui/ui/widgets/transfer_panel.py`

---

## 2026-03-22 — ConnectionDialog: red border highlight on empty required fields

**What:** Attempting to save a connection with Name, Host, or User left blank now
highlights those fields with a red border, in addition to showing the error text.

**Why:** The error label at the bottom of the dialog identified the problem as text, but
gave no visual cue about *which* field was empty — especially problematic if the form had
scrolled or the user had already spotted the message and forgot which field to fix.

**How:** Added `_set_field_error(widget, has_error)` which sets the `inputError` Qt
property and re-polishes the widget style. Called before construction so the highlight
appears immediately. Added `QLineEdit[inputError="true"]` rules to both `dark.qss` and
`light.qss` that render a red border.

**Files:**
- `src/sftp_ui/ui/dialogs/connection_dialog.py`
- `src/sftp_ui/styling/themes/dark.qss`
- `src/sftp_ui/styling/themes/light.qss`

---

## 2026-03-22 — Sync dialog: feedback when Upload/Download is clicked with nothing checked

**What:** Clicking "Upload selected" or "Download selected" with no files checked now shows
an explanatory message in the summary label instead of silently doing nothing.

**Why:** The previous behaviour was a silent no-op — the dialog stayed open, nothing
happened, and the user got no indication of why. Adding a short inline message ("No checked
files to upload") makes the state obvious without a modal dialog.

**How:** Changed `_do_upload` and `_do_download` to set `self._summary_label.setText(…)`
and return early when `jobs` is empty, instead of just `return`ing silently.

**Files:**
- `src/sftp_ui/ui/dialogs/sync_dialog.py`

---

## 2026-03-22 — F5 shortcut for remote refresh

**What:** Added `QShortcut(QKeySequence("F5"), self)` in `MainWindow._connect_signals()`,
wired to `self._remote_panel.refresh` (the same target as the existing Ctrl+R shortcut).

**Why:** F5 is the industry-standard key for "refresh" in every file manager and browser.
Ctrl+R was already present but undiscoverable; F5 satisfies muscle memory without removing
the existing shortcut.

**How:** One line added in `_connect_signals()` alongside the existing Ctrl+R binding.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — Toolbar buttons: add missing tooltips

**What:** Added `setToolTip()` calls to Connect, Disconnect, New, Edit, Delete, Manage, and
Refresh toolbar buttons. The Sync button already had a tooltip.

**Why:** Toolbar buttons have single-word or icon-only labels. Without tooltips, keyboard
shortcuts and exact purpose are invisible to new users. Qt shows tooltips automatically on
hover.

**How:** Called `setToolTip(...)` on each button immediately after construction in
`_build_toolbar()`. The Refresh tooltip mentions both F5 and Ctrl+R; Connect/Disconnect
mention Ctrl+K.

**Files:**
- `src/sftp_ui/ui/main_window.py`

---

## 2026-03-22 — New Folder / New File / Rename: reject names containing path separators

**What:** The New Folder, New File, and Rename dialogs in both the local panel and the remote
panel now show a warning and abort if the entered name contains a `/` (or the OS path
separator on non-POSIX systems).

**Why:** `os.path.join(cwd, "foo/bar")` and `PurePosixPath(cwd) / "foo/bar"` silently
produce multi-segment paths. On the local side this creates (or renames to) a file inside a
nested subdirectory. On the remote side the SFTP server receives a compound path that most
servers reject with a cryptic error. The user almost certainly intended a literal name
containing a slash — which is invalid on every common filesystem.

**How:** Added a `if "/" in name.strip():` guard (plus an `os.sep` guard for local
operations on non-POSIX) in `_do_new_folder`, `_do_new_file`, and `_do_rename` in both
`LocalPanel` and `RemotePanel`. Failing the check shows a `QMessageBox.warning` and returns
before any filesystem/SFTP call is made.

**Files:**
- `src/sftp_ui/ui/panels/local_panel.py`
- `src/sftp_ui/ui/panels/remote_panel.py`

---

## 2026-03-22 — Empty-state overlay: replace hardcoded dark colors with theme-aware styles

**What:** The "No connection" overlay shown over the remote file table no longer hardcodes
Catppuccin Mocha colors (`#1e1e2e`, `#313244`, `#45475a`). The background now uses
`self.palette().color(self.backgroundRole())` and the icon/title/hint labels are controlled
by QSS object-name rules added to both theme files.

**Why:** In the light theme the overlay previously rendered a near-black background
(`#1e1e2e`) with dark-grey text (`#313244`, `#45475a`), making the placeholder text
effectively invisible — dark-on-dark in a white-themed window.

**How:** Assigned `objectName` values (`empty-state-overlay`, `empty-state-icon`,
`empty-state-title`, `empty-state-hint`) to the overlay widget and its labels. Removed
inline `setStyleSheet` color rules from the labels. Added matching QSS blocks to
`dark.qss` (retaining the original dark palette) and `light.qss` (using light palette
equivalents: `#ccd0da` icon, `#8c8fa1` title, `#acb0be` hint on a `#ffffff` background).

**Files:**
- `src/sftp_ui/ui/panels/remote_panel.py`
- `src/sftp_ui/styling/themes/dark.qss`
- `src/sftp_ui/styling/themes/light.qss`
