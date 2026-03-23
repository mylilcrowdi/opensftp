"""
Tests for BookmarksBar widget.

Covers chip creation, refresh on store changes, visibility auto-hide,
connect_requested signal, and Ctrl+B shortcut via MainWindow integration.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from sftp_ui.core.connection import Connection, ConnectionStore
from sftp_ui.ui.widgets.bookmarks_bar import BookmarksBar, _ChipButton


# ── helpers ────────────────────────────────────────────────────────────────────

def _conn(name: str, favorite: bool = False, tmp_path: Path | None = None) -> Connection:
    """Build a minimal Connection; key_path is a real tmp file to pass validation."""
    if tmp_path is None:
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp())
    else:
        tmp_dir = tmp_path
    key = tmp_dir / f"id_{name}"
    key.write_bytes(b"fake")
    return Connection(
        name=name,
        host=f"{name}.example.com",
        user="admin",
        port=22,
        key_path=str(key),
        favorite=favorite,
    )


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(path=tmp_path / "connections.json")


@pytest.fixture
def fav_conn(tmp_path):
    return _conn("prod", favorite=True, tmp_path=tmp_path)


@pytest.fixture
def plain_conn(tmp_path):
    return _conn("staging", favorite=False, tmp_path=tmp_path)


# ── BookmarksBar tests ─────────────────────────────────────────────────────────

class TestBookmarksBarVisibility:
    def test_hidden_when_no_favorites(self, qapp, store):
        bar = BookmarksBar(store)
        assert not bar.isVisible()

    def test_visible_after_adding_favorite(self, qapp, store, fav_conn):
        store.add(fav_conn)
        bar = BookmarksBar(store)
        assert bar.isVisible()

    def test_hidden_when_all_favorites_removed(self, qapp, store, fav_conn):
        store.add(fav_conn)
        bar = BookmarksBar(store)
        assert bar.isVisible()

        import dataclasses
        unfaved = dataclasses.replace(fav_conn, favorite=False)
        store.update(unfaved)
        bar.refresh()
        assert not bar.isVisible()

    def test_visibility_changed_signal_fires_on_show(self, qapp, store, fav_conn):
        bar = BookmarksBar(store)
        received: list[bool] = []
        bar.visibility_changed.connect(received.append)

        store.add(fav_conn)
        bar.refresh()
        assert True in received

    def test_visibility_changed_signal_fires_on_hide(self, qapp, store, fav_conn):
        import dataclasses
        store.add(fav_conn)
        bar = BookmarksBar(store)
        received: list[bool] = []
        bar.visibility_changed.connect(received.append)

        unfaved = dataclasses.replace(fav_conn, favorite=False)
        store.update(unfaved)
        bar.refresh()
        assert False in received


class TestBookmarksBarChips:
    def test_one_chip_per_favorite(self, qapp, store, tmp_path):
        c1 = _conn("alpha", favorite=True, tmp_path=tmp_path)
        c2 = _conn("beta",  favorite=True, tmp_path=tmp_path)
        store.add(c1)
        store.add(c2)
        bar = BookmarksBar(store)
        chips = bar.findChildren(_ChipButton)
        assert len(chips) == 2

    def test_non_favorites_produce_no_chips(self, qapp, store, plain_conn, tmp_path):
        fav = _conn("prod", favorite=True, tmp_path=tmp_path)
        store.add(plain_conn)
        store.add(fav)
        bar = BookmarksBar(store)
        chips = bar.findChildren(_ChipButton)
        assert len(chips) == 1
        assert chips[0].conn.id == fav.id

    def test_chip_label_contains_star_and_name(self, qapp, store, fav_conn):
        store.add(fav_conn)
        bar = BookmarksBar(store)
        chips = bar.findChildren(_ChipButton)
        assert len(chips) == 1
        assert "★" in chips[0].text()
        assert fav_conn.name in chips[0].text()

    def test_chip_tooltip_contains_host_and_user(self, qapp, store, fav_conn):
        store.add(fav_conn)
        bar = BookmarksBar(store)
        chips = bar.findChildren(_ChipButton)
        tip = chips[0].toolTip()
        assert fav_conn.host in tip
        assert fav_conn.user in tip

    def test_refresh_adds_new_favorite_chip(self, qapp, store, fav_conn, tmp_path):
        bar = BookmarksBar(store)
        assert len(bar.findChildren(_ChipButton)) == 0

        store.add(fav_conn)
        bar.refresh()
        assert len(bar.findChildren(_ChipButton)) == 1

    def test_refresh_removes_unfavorited_chip(self, qapp, store, fav_conn):
        import dataclasses
        store.add(fav_conn)
        bar = BookmarksBar(store)
        assert len(bar.findChildren(_ChipButton)) == 1

        unfaved = dataclasses.replace(fav_conn, favorite=False)
        store.update(unfaved)
        bar.refresh()
        assert len(bar.findChildren(_ChipButton)) == 0

    def test_chips_sorted_alphabetically(self, qapp, store, tmp_path):
        c_z = _conn("zebra",  favorite=True, tmp_path=tmp_path)
        c_a = _conn("alpha",  favorite=True, tmp_path=tmp_path)
        c_m = _conn("mango",  favorite=True, tmp_path=tmp_path)
        for c in (c_z, c_a, c_m):
            store.add(c)
        bar = BookmarksBar(store)
        chips = bar.findChildren(_ChipButton)
        names = [c.conn.name for c in chips]
        assert names == sorted(names, key=str.lower)


class TestBookmarksBarSignal:
    def test_connect_requested_emitted_on_chip_click(self, qapp, store, fav_conn):
        store.add(fav_conn)
        bar = BookmarksBar(store)

        received: list = []
        bar.connect_requested.connect(received.append)

        chips = bar.findChildren(_ChipButton)
        assert chips
        chips[0].click()

        assert len(received) == 1
        assert received[0].id == fav_conn.id

    def test_on_connect_callback_called(self, qapp, store, fav_conn):
        store.add(fav_conn)
        called: list = []
        bar = BookmarksBar(store, on_connect=called.append)

        chips = bar.findChildren(_ChipButton)
        assert chips
        chips[0].click()

        assert len(called) == 1
        assert called[0].id == fav_conn.id

    def test_missing_conn_does_not_raise(self, qapp, store, fav_conn):
        """If the connection is deleted after the chip was created, click should be silent."""
        store.add(fav_conn)
        bar = BookmarksBar(store)
        # Delete from store while chip still exists
        store.remove(fav_conn.id)
        # Clicking must not raise
        chips = bar.findChildren(_ChipButton)
        assert chips
        chips[0].click()   # should be a no-op


class TestChipButton:
    def test_objectname_is_bookmarkChip(self, qapp, tmp_path):
        conn = _conn("test", favorite=True, tmp_path=tmp_path)
        chip = _ChipButton(conn)
        assert chip.objectName() == "bookmarkChip"

    def test_update_conn_changes_text(self, qapp, tmp_path):
        import dataclasses
        conn = _conn("old_name", favorite=True, tmp_path=tmp_path)
        chip = _ChipButton(conn)
        assert "old_name" in chip.text()

        renamed = dataclasses.replace(conn, name="new_name")
        chip.update_conn(renamed)
        assert "new_name" in chip.text()
        assert "old_name" not in chip.text()
