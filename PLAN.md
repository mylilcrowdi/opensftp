# SFTP UI — Launch Plan

> **Goal:** ship a polished, notarized macOS SFTP client as a one-time-purchase indie product.
>
> **Stack:** Python · PySide6 · paramiko · Briefcase
> **Target price:** €25 one-time · **Platform:** direct sale (Gumroad / Lemon Squeezy)
> **Timeline estimate:** 1–2 weeks to sellable v1.0

This file is the single source of truth for what needs to happen.
Agents and humans work from this file. Mark items `[x]` when done.
Each phase is independent enough to be parallelised where noted.

---

## Phase 0 — Repository Hygiene  *(can start immediately)*

These are blockers for everything else. Cheap, fast, do first.

- [x] **Delete legacy files** — `main.py`, `run.sh`, `test_window.py`, root-level `sftp_ui/` dir

- [x] **Fix requirements.txt** — replaced `PyQt6` with `PySide6>=6.5`, `paramiko>=3.0`, `cryptography>=42`

- [ ] **Fill in pyproject.toml metadata** ← *needs your real email + domain + GitHub URL*
      - `author_email` — add your email
      - `bundle = "com.sftpui"` — change to your own reverse-domain if desired
      - `url` — add GitHub repo URL after creating it

- [x] **Create LICENSE file in root** — MIT, 2025

- [x] **Write CHANGELOG.md** — v0.1.0 Unreleased with full feature list

---

## Phase 1 — Open Source Readiness  *(parallel with Phase 2)*

The code is already clean. This is documentation and discoverability.

- [x] **Rewrite README.md in English** — feature list, build instructions, architecture table, decisions log

- [x] **Add GitHub Actions CI** — `.github/workflows/ci.yml`, Python 3.11 + 3.12, headless `QT_QPA_PLATFORM=offscreen`

- [ ] **Create GitHub repository** ← *manual step — needs your GitHub account*
      ```bash
      gh repo create sftp-ui --public --description "Modern dual-pane SFTP client for macOS"
      git remote add origin https://github.com/YOUR_USERNAME/sftp-ui.git
      git push -u origin main
      ```

- [ ] **Add screenshots / demo GIF to README** ← *manual step — record with Kap or QuickTime*
      Place in `docs/` folder, link from README. One screenshot of main window is enough for v1.

---

## Phase 2 — Bug Fixes & Polish  *(parallel with Phase 1)*

Known issues to fix before selling. A paying customer hitting any of these will ask for a refund.

- [x] **First-launch empty state** — `_EmptyStateOverlay` added to remote panel; shows "No connection" + hint text; hides on connect, reappears on disconnect

- [x] **Connection error dialog** — manual connects show a `QMessageBox.Critical` with host:port + error detail; auto-reconnect failures stay silent (status bar only)

- [x] **Upload progress for single small files** — minimum 2.5 s visibility enforced in `_maybe_hide` using `_first_shown_at` timestamp; timer reschedules itself for the remaining gap

- [ ] **Sync dialog smoke-test**
      The parallel BFS remote walk + scan is implemented but not battle-tested.
      Test with: empty dir, flat dir, deeply nested dir, dir with 1000+ files, cancelled mid-scan.
      Fix any crashes or hangs.

- [x] **Keyboard shortcuts** — `Cmd+R` refresh, `Cmd+K` connect/disconnect toggle, `Cmd+N` new connection
      Still missing: `Delete` key in remote panel, `Space` in sync dialog — add in next pass

- [x] **Window minimum size** — `setMinimumSize(900, 600)` added to MainWindow

- [ ] **App icon**
      Briefcase ships a default icon. Create a custom one.
      Format: 1024×1024 PNG → Briefcase converts to .icns.
      Place at: `src/sftp_ui/resources/icon.png` and reference in pyproject.toml:
      ```toml
      icon = "src/sftp_ui/resources/icon"
      ```

---

## Phase 3 — Distribution Build  *(after Phase 0 + 2)*

Everything needed to get from source to a downloadable `.dmg`.

- [ ] **Apple Developer account**
      Required for notarization. Cost: $99/yr at developer.apple.com.
      Without this, every user gets a Gatekeeper "unidentified developer" warning.

- [ ] **Set up code signing**
      Once enrolled, create a "Developer ID Application" certificate in Keychain Access.
      Briefcase uses it automatically:
      ```bash
      briefcase package macOS --identity "Developer ID Application: Your Name (TEAMID)"
      ```

- [ ] **Notarize the build**
      ```bash
      briefcase package macOS          # builds signed .dmg
      xcrun notarytool submit ...      # submit to Apple (or use briefcase package --notarize)
      xcrun stapler staple ...         # staple the ticket
      ```
      Result: a `.dmg` that macOS opens without any security warnings.

- [ ] **Test the .dmg on a clean machine**
      Download and run on a Mac that has never had Python or the dev env installed.
      Check: window appears, connection dialog works, SFTP connects, file transfer works.

- [ ] **Version the release**
      Bump `version = "1.0.0"` in pyproject.toml.
      Tag the git commit: `git tag v1.0.0`.
      Upload the .dmg as a GitHub Release asset.

---

## Phase 4 — Storefront & Sale  *(after Phase 3)*

- [ ] **Choose a platform**
      Recommendation: **Gumroad** for speed, **Lemon Squeezy** for better EU VAT handling.
      Both generate license keys automatically.

- [ ] **Create product listing**
      - Title: "SFTP UI — Native macOS SFTP Client"
      - Price: €25 one-time (test at €9 for early-access / launch discount)
      - Description: feature list, system requirements (macOS 12+), screenshots
      - Digital product: upload the notarized `.dmg`

- [ ] **License key activation (optional but recommended)**
      Gumroad provides a license key API. On first launch, prompt for a key,
      verify against `https://api.gumroad.com/v2/licenses/verify`,
      store result in `~/.config/sftp-ui/license.json`.
      Without this anyone can share the .dmg freely — your call on enforcement.

- [ ] **Update page** — link from README "Download" button to the Gumroad/LemonSqueezy page

- [ ] **Post launch announcement**
      - Hacker News "Show HN" post
      - r/macapps
      - X/Twitter with screenshot
      - Indie Hackers

---

## Phase 5 — Post-Launch  *(after first sales)*

Nice-to-haves that make the product stickier and justify future updates.

- [ ] **Auto-update mechanism**
      Simplest: check a `version.json` URL on launch, show a banner if newer version available.
      Full: integrate Sparkle framework via Briefcase plugin.

- [ ] **Windows / Linux support**
      PySide6 and paramiko are cross-platform. The main blocker is:
      - Remove `std-nslog` macOS-only dep (already guarded by `[macOS]` section)
      - Test font sizes on Windows (macOS renders at different DPI)
      - Briefcase builds `.msi` and `.AppImage` out of the box once tested

- [ ] **SSH agent / keychain integration**
      Currently passwords and passphrases are stored in plain JSON.
      Integrate macOS Keychain via `keyring` library for secure credential storage.

- [ ] **SFTP bookmarks / favorites**
      Let users star frequently-visited remote paths per connection.

- [ ] **File preview**
      Click an image/text file in the remote panel → preview without downloading.

- [ ] **Multiple simultaneous connections**
      Tab bar across the top — each tab is an independent remote panel + queue.

---

## Current Architecture (reference)

```
src/sftp_ui/
├── core/           ← zero Qt; testable headlessly
│   ├── connection.py      ConnectionStore → ~/.config/sftp-ui/connections.json
│   ├── sftp_client.py     paramiko wrapper; RemoteEntry dataclass
│   ├── transfer.py        TransferEngine (upload/download, resume)
│   ├── queue.py           TransferQueue (4 workers, auto-retry, cancel)
│   └── ui_state.py        UIState → ~/.config/sftp-ui/ui_state.json
├── styling/        ← hot-swappable QSS themes
├── animations/     ← named presets (fade_in, fade_out, …)
└── ui/
    ├── main_window.py     orchestrator; thread-safe _Signals bridge
    ├── panels/            local browser (QTreeWidget), remote browser (QTableView)
    ├── dialogs/           connection form, sync preview
    └── widgets/           transfer panel, status dot, skeleton, smooth progress bar
```

**Key architectural decisions:**
- Every SFTP background op gets its own dedicated `SFTPClient` — paramiko is not thread-safe
- Jobs pre-registered in TransferPanel via `job_enqueued` signal before workers start — ensures accurate batch progress from tick 0
- Upload uses two-phase: local walk first (panel appears immediately) → remote prep in background
- `was_connected` flag in UIState drives auto-reconnect on next launch

---

## Decisions Log

| Date | Decision | Reason |
|------|----------|--------|
| — | PySide6 over PyQt6 | LGPL license, better for commercial distribution |
| — | Briefcase over cx_Freeze / PyInstaller | Handles macOS Window Server access, code signing, notarization |
| — | Catppuccin Mocha palette | Consistent, modern, already widely known |
| — | MIT license | Maximum permissiveness; standard for indie tools |
| — | One-time purchase, no subscription | SFTP clients are a solved problem; SaaS would be wrong model |
| — | macOS-first, not cross-platform yet | Faster to ship; macOS users pay for software |
