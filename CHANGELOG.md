# Changelog

All notable changes to SFTP UI are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

## [0.1.0] — 2025

### Added
- Dual-pane browser: local filesystem (left) + remote SFTP (right)
- Multi-connection store with SSH key (Ed25519 / RSA / ECDSA) and password auth
- Parallel transfer queue — 4 concurrent workers, auto-retry (5×, 3 s delay), resume on reconnect
- Upload via drag-and-drop with animated drop overlay
- Two-phase upload: local scan → panel appears immediately, remote prep in background
- Overwrite conflict dialog when remote file already exists (Overwrite All / Skip / Cancel)
- Directory sync preview — parallel BFS remote walk, side-by-side diff by size and mtime
- Smooth progress bar (OutCubic easing), animated status dot (pulse / pop / shake)
- Skeleton loading overlay on remote panel while listing is in-flight
- Auto-reconnect on startup if the previous session was active
- Hot-swappable dark / light themes (Catppuccin Mocha palette)
- Persistent UI state — last local path, last remote path per connection, last connection
- Briefcase packaging — produces notarization-ready macOS .app / .dmg
