"""
Tests for TransferPanel and its helpers.

Covers: _fmt_size, _fmt_speed, _fmt_eta (pure functions),
        _JobItem.refresh() state display logic,
        TransferPanel.add_job / _all_settled / _update_toggle_label /
        overflow label / _clear.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sftp_ui.core.transfer import TransferDirection, TransferJob, TransferState
from sftp_ui.ui.widgets.transfer_panel import (
    TransferPanel,
    _JobItem,
    _fmt_eta,
    _fmt_size,
    _fmt_speed,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _upload_job(name: str = "file.bin", state: TransferState = TransferState.PENDING) -> TransferJob:
    job = TransferJob(local_path=f"/local/{name}", remote_path=f"/remote/{name}",
                      direction=TransferDirection.UPLOAD)
    job.state = state
    return job


def _download_job(name: str = "file.bin", state: TransferState = TransferState.PENDING) -> TransferJob:
    job = TransferJob(local_path=f"/local/{name}", remote_path=f"/remote/{name}",
                      direction=TransferDirection.DOWNLOAD)
    job.state = state
    return job


# ── _fmt_size ──────────────────────────────────────────────────────────────────

class TestFmtSize:
    def test_zero_bytes(self):
        assert _fmt_size(0) == "0.0 B"

    def test_small_bytes(self):
        result = _fmt_size(512)
        assert "B" in result
        assert "512" in result

    def test_exactly_1kb(self):
        assert _fmt_size(1024) == "1.0 KB"

    def test_fractional_kb(self):
        result = _fmt_size(1536)  # 1.5 KB
        assert "KB" in result
        assert "1.5" in result

    def test_exactly_1mb(self):
        assert _fmt_size(1024 * 1024) == "1.0 MB"

    def test_exactly_1gb(self):
        assert _fmt_size(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        result = _fmt_size(1024 ** 4)
        assert "TB" in result

    def test_large_byte_value(self):
        result = _fmt_size(999)
        assert "B" in result
        assert "KB" not in result

    def test_returns_string(self):
        assert isinstance(_fmt_size(100), str)

    def test_unit_step_at_1024(self):
        # 1023 stays in B, 1024 moves to KB
        assert "B" in _fmt_size(1023)
        assert "KB" in _fmt_size(1024)


# ── _fmt_speed ─────────────────────────────────────────────────────────────────

class TestFmtSpeed:
    def test_appends_per_second(self):
        result = _fmt_speed(1024)
        assert "/s" in result

    def test_zero_speed(self):
        result = _fmt_speed(0)
        assert "/s" in result

    def test_mb_per_second(self):
        result = _fmt_speed(1024 * 1024)
        assert "MB/s" in result

    def test_high_speed(self):
        result = _fmt_speed(100 * 1024 * 1024)  # 100 MB/s
        assert "/s" in result

    def test_returns_string(self):
        assert isinstance(_fmt_speed(512), str)


# ── _fmt_eta ───────────────────────────────────────────────────────────────────

class TestFmtEta:
    def test_zero_bps_returns_empty(self):
        assert _fmt_eta(1000, 0.0) == ""

    def test_zero_remaining_returns_empty(self):
        assert _fmt_eta(0, 1024.0) == ""

    def test_seconds_only(self):
        # 50 bytes remaining at 1 B/s → ~50s
        result = _fmt_eta(50, 1.0)
        assert "s" in result
        assert "50" in result

    def test_minutes_format(self):
        # 120 bytes at 1 B/s → ~2m
        result = _fmt_eta(120, 1.0)
        assert "m" in result

    def test_eta_under_60s_no_minutes(self):
        result = _fmt_eta(30, 1.0)
        assert "m" not in result
        assert "s" in result

    def test_returns_string(self):
        assert isinstance(_fmt_eta(100, 10.0), str)

    def test_both_zero_returns_empty(self):
        assert _fmt_eta(0, 0.0) == ""

    def test_negative_bps_returns_empty(self):
        assert _fmt_eta(100, -1.0) == ""


# ── _JobItem.refresh() ─────────────────────────────────────────────────────────

class TestJobItemRefresh:
    def test_pending_shows_pending_text(self, qapp):
        job = _upload_job(state=TransferState.PENDING)
        item = _JobItem(job)
        assert item._status.text() == "Pending"

    def test_done_shows_done_text(self, qapp):
        job = _upload_job(state=TransferState.DONE)
        item = _JobItem(job)
        assert item._status.text() == "Done"

    def test_failed_shows_failed_text(self, qapp):
        job = _upload_job(state=TransferState.FAILED)
        item = _JobItem(job)
        assert "Failed" in item._status.text()

    def test_cancelled_shows_cancelled_text(self, qapp):
        job = _upload_job(state=TransferState.CANCELLED)
        item = _JobItem(job)
        assert "Cancelled" in item._status.text()

    def test_running_upload_shows_up_arrow(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        item = _JobItem(job)
        assert "↑" in item._icon.text()

    def test_running_download_shows_down_arrow(self, qapp):
        job = _download_job(state=TransferState.RUNNING)
        item = _JobItem(job)
        assert "↓" in item._icon.text()

    def test_resume_btn_visible_on_failed(self, qapp):
        job = _upload_job(state=TransferState.FAILED)
        item = _JobItem(job)
        assert not item._resume_btn.isHidden()

    def test_resume_btn_hidden_on_done(self, qapp):
        job = _upload_job(state=TransferState.DONE)
        item = _JobItem(job)
        assert item._resume_btn.isHidden()

    def test_resume_btn_hidden_on_pending(self, qapp):
        job = _upload_job(state=TransferState.PENDING)
        item = _JobItem(job)
        assert item._resume_btn.isHidden()

    def test_resume_btn_visible_on_user_cancelled(self, qapp):
        # CANCELLED without error = user cancelled = can resume
        job = _upload_job(state=TransferState.CANCELLED)
        job.error = None
        item = _JobItem(job)
        assert not item._resume_btn.isHidden()

    def test_resume_btn_hidden_on_cancelled_with_error(self, qapp):
        # CANCELLED with error = intentional skip = no resume
        job = _upload_job(state=TransferState.CANCELLED)
        job.error = "skipped"
        item = _JobItem(job)
        assert item._resume_btn.isHidden()

    def test_name_label_shows_filename(self, qapp):
        job = _upload_job(name="important.csv", state=TransferState.PENDING)
        item = _JobItem(job)
        assert "important.csv" in item._name.text()

    def test_failed_shows_error_message(self, qapp):
        job = _upload_job(state=TransferState.FAILED)
        job.error = "network timeout"
        item = _JobItem(job)
        item.refresh()
        assert "network timeout" in item._status.text()

    def test_state_change_reflected_on_refresh(self, qapp):
        job = _upload_job(state=TransferState.PENDING)
        item = _JobItem(job)
        assert item._status.text() == "Pending"
        job.state = TransferState.DONE
        item.refresh()
        assert item._status.text() == "Done"


# ── TransferPanel — add_job / visibility ───────────────────────────────────────

class TestTransferPanelAddJob:
    def test_add_job_makes_panel_visible(self, qapp):
        panel = TransferPanel()
        job = _upload_job()
        panel.add_job(job)
        assert panel.isVisible()

    def test_add_job_registers_in_jobs_list(self, qapp):
        panel = TransferPanel()
        job = _upload_job()
        panel.add_job(job)
        assert job in panel._jobs

    def test_add_multiple_jobs(self, qapp):
        panel = TransferPanel()
        for i in range(5):
            panel.add_job(_upload_job(name=f"file{i}.bin"))
        assert len(panel._jobs) == 5

    def test_toggle_label_shows_job_count(self, qapp):
        panel = TransferPanel()
        panel.add_job(_upload_job(name="a.bin"))
        panel.add_job(_upload_job(name="b.bin"))
        assert "2" in panel._queue_toggle.text()

    def test_panel_initially_hidden(self, qapp):
        panel = TransferPanel()
        assert not panel.isVisible()


# ── TransferPanel — _all_settled ───────────────────────────────────────────────

class TestTransferPanelAllSettled:
    def test_empty_jobs_is_settled(self, qapp):
        panel = TransferPanel()
        assert panel._all_settled()

    def test_all_done_is_settled(self, qapp):
        panel = TransferPanel()
        for state in (TransferState.DONE, TransferState.FAILED, TransferState.CANCELLED):
            job = _upload_job(state=state)
            panel._jobs.append(job)
        assert panel._all_settled()

    def test_pending_job_not_settled(self, qapp):
        panel = TransferPanel()
        panel._jobs.append(_upload_job(state=TransferState.PENDING))
        assert not panel._all_settled()

    def test_running_job_not_settled(self, qapp):
        panel = TransferPanel()
        panel._jobs.append(_upload_job(state=TransferState.RUNNING))
        assert not panel._all_settled()

    def test_mixed_settled_and_pending(self, qapp):
        panel = TransferPanel()
        panel._jobs.append(_upload_job(state=TransferState.DONE))
        panel._jobs.append(_upload_job(state=TransferState.PENDING))
        assert not panel._all_settled()


# ── TransferPanel — overflow label ────────────────────────────────────────────

class TestTransferPanelOverflow:
    def test_no_overflow_label_below_max(self, qapp):
        panel = TransferPanel()
        for i in range(5):
            panel.add_job(_upload_job(name=f"f{i}.bin"))
        assert not panel._overflow_label.isVisible()

    def test_overflow_label_shown_above_max(self, qapp):
        panel = TransferPanel()
        max_items = TransferPanel._MAX_VISIBLE_ITEMS
        for i in range(max_items + 5):
            panel.add_job(_upload_job(name=f"f{i}.bin"))
        assert panel._overflow_label.isVisible()
        assert "5" in panel._overflow_label.text()

    def test_overflow_label_shows_hidden_count(self, qapp):
        panel = TransferPanel()
        max_items = TransferPanel._MAX_VISIBLE_ITEMS
        extra = 10
        for i in range(max_items + extra):
            panel.add_job(_upload_job(name=f"f{i}.bin"))
        assert str(extra) in panel._overflow_label.text()


# ── TransferPanel — _clear ────────────────────────────────────────────────────

class TestTransferPanelClear:
    def test_clear_empties_jobs(self, qapp):
        panel = TransferPanel()
        panel.add_job(_upload_job())
        panel._clear()
        assert len(panel._jobs) == 0

    def test_clear_empties_job_items(self, qapp):
        panel = TransferPanel()
        panel.add_job(_upload_job())
        panel._clear()
        assert len(panel._job_items) == 0

    def test_clear_resets_progress_bar(self, qapp):
        panel = TransferPanel()
        panel.add_job(_upload_job())
        panel._progress_bar.setValue(75)
        panel._clear()
        assert panel._progress_bar.value() == 0

    def test_clear_resets_pct_label(self, qapp):
        panel = TransferPanel()
        panel._pct_label.setText("75%")
        panel._clear()
        assert panel._pct_label.text() == "0%"

    def test_clear_hides_overflow_label(self, qapp):
        panel = TransferPanel()
        max_items = TransferPanel._MAX_VISIBLE_ITEMS
        for i in range(max_items + 3):
            panel.add_job(_upload_job(name=f"f{i}.bin"))
        assert panel._overflow_label.isVisible()
        panel._clear()
        assert not panel._overflow_label.isVisible()

    def test_clear_stops_speed_timer(self, qapp):
        panel = TransferPanel()
        panel._speed_timer.start()
        panel._clear()
        assert not panel._speed_timer.isActive()


# ── TransferPanel — _update_toggle_label ──────────────────────────────────────

class TestTransferPanelToggleLabel:
    def test_toggle_label_shows_pending_count(self, qapp):
        panel = TransferPanel()
        panel._jobs = [
            _upload_job(state=TransferState.PENDING),
            _upload_job(state=TransferState.PENDING),
            _upload_job(state=TransferState.DONE),
        ]
        panel._update_toggle_label()
        assert "2 pending" in panel._queue_toggle.text()

    def test_toggle_label_shows_failed_count(self, qapp):
        panel = TransferPanel()
        panel._jobs = [
            _upload_job(state=TransferState.FAILED),
            _upload_job(state=TransferState.DONE),
        ]
        panel._update_toggle_label()
        assert "1 failed" in panel._queue_toggle.text()

    def test_toggle_label_no_pending_no_failed_if_all_done(self, qapp):
        panel = TransferPanel()
        panel._jobs = [_upload_job(state=TransferState.DONE)]
        panel._update_toggle_label()
        text = panel._queue_toggle.text()
        assert "pending" not in text
        assert "failed" not in text

    def test_toggle_label_cancelled_counted_as_failed(self, qapp):
        panel = TransferPanel()
        panel._jobs = [_upload_job(state=TransferState.CANCELLED)]
        panel._update_toggle_label()
        assert "1 failed" in panel._queue_toggle.text()
