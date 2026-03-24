# Contributing to openSFTP

Thanks for your interest. Read this before opening a PR.

---

## Repository structure

This is the **open source core** (`mylilcrowdi/opensftp`, MIT).

A separate private repository (`opensftp-pro`) contains Pro-only extensions
(cloud storage, SSH tunnels, extra themes, multi-tab, sync profiles, etc.)
and is not publicly available. The Pro build bundles both repos into a
signed native installer sold at renemurrell.de/software/opensftp.

**If you're a maintainer**, see [Maintainer workflow](#maintainer-workflow) below.

---

## What belongs here

- Core SFTP functionality (client, transfers, queue, sync)
- Free-tier UI (single tab, dual-pane, Dark + Light themes)
- Bug fixes that affect both free and Pro users
- Tests for all of the above

**Does not belong here:**
- Cloud storage (S3/GCS/Backblaze)
- SSH tunnels, SSH agent
- System keychain integration
- Multi-tab connections, bookmarks bar
- Nord / Dracula / Solarized Dark themes
- Remote search, file permissions editor
- Anything that gates on `edition.PRO`

If you're unsure, open an issue first.

---

## Getting started

```bash
git clone https://github.com/mylilcrowdi/opensftp.git
cd opensftp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Run tests:

```bash
pytest tests/ --ignore=tests/test_e2e_screenshots.py -q
```

---

## Submitting a PR

1. Fork the repo, create a branch off `main`
2. Make your change, add tests
3. `pytest tests/ --ignore=tests/test_e2e_screenshots.py -q` — must pass
4. `core/` must stay Qt-free (no `PySide6` imports)
5. Open the PR against `main`

---

## Maintainer workflow

> This section is for René / `mylilcrowdi` only.

The working directory is `/home/async/sftp-ui-work/`. Two remotes, two branches:

```
origin  →  mylilcrowdi/opensftp      (public,  branch: main)
pro     →  mylilcrowdi/opensftp-pro  (private, branch: main)
```

The `pro` branch is always rebased on top of `main`.
Pro commits are stacked cleanly on top — the diff between the two branches
is exactly the Pro extensions, nothing else.

### Bug fix or free feature (lands in both)

```bash
git checkout main
# make the change
git commit -m "fix: ..."
git push origin main

git checkout pro
git rebase main
git push pro main --force-with-lease
```

### Pro-only feature

```bash
git checkout pro
# add to Pro modules only
git commit -m "feat(pro): ..."
git push pro main
# main is untouched
```

### Open source only feature

```bash
git checkout main
git commit -m "feat: ..."
git push origin main

git checkout pro
git rebase main          # picks up the new commit automatically
git push pro main --force-with-lease
```

### One-time remote setup (already done)

```bash
git remote add pro https://github.com/mylilcrowdi/opensftp-pro.git
git checkout -b pro
git push pro pro:main
```

### Why rebase and not merge?

Merge creates noise. Rebase keeps the Pro branch as a clean linear extension
of `main` — always easy to see exactly what Pro adds over the free version.

---

## Code style

- Python 3.11+, type hints where it helps readability
- `core/` has zero Qt imports — keep it that way
- No external formatters enforced, but stay consistent with surrounding code

---

## License

By submitting a PR you agree that your contribution is licensed under the
MIT License (see [LICENSE](LICENSE)).
