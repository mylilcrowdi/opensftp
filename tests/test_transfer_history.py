"""
Tests for TransferHistory — persistent log of completed transfers.

The transfer history records every completed, failed, and cancelled transfer
so users can review what happened across sessions. Stored as JSON lines.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sftp_ui.core.transfer import TransferDirection, TransferJob, TransferState
from sftp_ui.core.transfer_history import TransferHistory


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_job(
    name: str = "file.bin",
    direction: TransferDirection = TransferDirection.UPLOAD,
    state: TransferState = TransferState.DONE,
    total_bytes: int = 1024,
    error: str | None = None,
) -> TransferJob:
    job = TransferJob(
        local_path=f"/local/{name}",
        remote_path=f"/remote/{name}",
        direction=direction,
    )
    job.state = state
    job.total_bytes = total_bytes
    job.bytes_done = total_bytes if state == TransferState.DONE else 0
    job.error = error
    job.finished_at = time.time()
    return job


# ── Recording transfers ──────────────────────────────────────────────────────

class TestTransferHistoryRecord:
    def test_record_adds_entry(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job()
        history.record(job)
        assert len(history.entries()) == 1

    def test_record_multiple_entries(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        for i in range(5):
            history.record(_make_job(name=f"file{i}.bin"))
        assert len(history.entries()) == 5

    def test_record_stores_filename(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job(name="report.csv")
        history.record(job)
        entry = history.entries()[0]
        assert entry["filename"] == "report.csv"

    def test_record_stores_direction(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job(direction=TransferDirection.DOWNLOAD)
        history.record(job)
        entry = history.entries()[0]
        assert entry["direction"] == "download"

    def test_record_stores_state(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job(state=TransferState.FAILED, error="timeout")
        history.record(job)
        entry = history.entries()[0]
        assert entry["state"] == "failed"

    def test_record_stores_size(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job(total_bytes=1024 * 1024)
        history.record(job)
        entry = history.entries()[0]
        assert entry["total_bytes"] == 1024 * 1024

    def test_record_stores_error(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job(state=TransferState.FAILED, error="connection reset")
        history.record(job)
        entry = history.entries()[0]
        assert entry["error"] == "connection reset"

    def test_record_stores_paths(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job = _make_job(name="data.bin")
        history.record(job)
        entry = history.entries()[0]
        assert entry["local_path"] == "/local/data.bin"
        assert entry["remote_path"] == "/remote/data.bin"

    def test_record_stores_timestamp(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        before = time.time()
        job = _make_job()
        history.record(job)
        entry = history.entries()[0]
        assert entry["finished_at"] >= before


# ── Persistence ──────────────────────────────────────────────────────────────

class TestTransferHistoryPersistence:
    def test_entries_persist_across_instances(self, tmp_path):
        path = tmp_path / "history.jsonl"
        h1 = TransferHistory(path)
        job_a = _make_job(name="a.bin")
        job_a.finished_at = 1000.0
        h1.record(job_a)
        job_b = _make_job(name="b.bin")
        job_b.finished_at = 2000.0
        h1.record(job_b)

        h2 = TransferHistory(path)
        assert len(h2.entries()) == 2
        # newest first
        assert h2.entries()[0]["filename"] == "b.bin"
        assert h2.entries()[1]["filename"] == "a.bin"

    def test_file_is_jsonl_format(self, tmp_path):
        path = tmp_path / "history.jsonl"
        history = TransferHistory(path)
        history.record(_make_job(name="x.bin"))
        history.record(_make_job(name="y.bin"))

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "filename" in parsed

    def test_empty_file_returns_no_entries(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text("")
        history = TransferHistory(path)
        assert history.entries() == []

    def test_nonexistent_file_returns_no_entries(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        history = TransferHistory(path)
        assert history.entries() == []

    def test_corrupted_line_skipped(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text('{"filename":"ok.bin","direction":"upload","state":"done","total_bytes":100,"local_path":"/a","remote_path":"/b","finished_at":1.0,"error":null}\nnot json\n')
        history = TransferHistory(path)
        entries = history.entries()
        assert len(entries) == 1
        assert entries[0]["filename"] == "ok.bin"


# ── Filtering ────────────────────────────────────────────────────────────────

class TestTransferHistoryFilter:
    def test_filter_by_state(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        history.record(_make_job(name="ok.bin", state=TransferState.DONE))
        history.record(_make_job(name="fail.bin", state=TransferState.FAILED, error="err"))
        history.record(_make_job(name="ok2.bin", state=TransferState.DONE))

        failed = history.entries(state="failed")
        assert len(failed) == 1
        assert failed[0]["filename"] == "fail.bin"

    def test_filter_by_direction(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        history.record(_make_job(name="up.bin", direction=TransferDirection.UPLOAD))
        history.record(_make_job(name="down.bin", direction=TransferDirection.DOWNLOAD))

        downloads = history.entries(direction="download")
        assert len(downloads) == 1
        assert downloads[0]["filename"] == "down.bin"

    def test_entries_returned_newest_first(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        job1 = _make_job(name="first.bin")
        job1.finished_at = 1000.0
        history.record(job1)
        job2 = _make_job(name="second.bin")
        job2.finished_at = 2000.0
        history.record(job2)

        entries = history.entries()
        assert entries[0]["filename"] == "second.bin"
        assert entries[1]["filename"] == "first.bin"


# ── Clear / limit ────────────────────────────────────────────────────────────

class TestTransferHistoryClear:
    def test_clear_removes_all_entries(self, tmp_path):
        path = tmp_path / "history.jsonl"
        history = TransferHistory(path)
        for i in range(3):
            history.record(_make_job(name=f"f{i}.bin"))
        history.clear()
        assert history.entries() == []
        assert not path.exists() or path.read_text().strip() == ""

    def test_limit_returns_n_most_recent(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        for i in range(10):
            job = _make_job(name=f"f{i}.bin")
            job.finished_at = float(i)
            history.record(job)

        recent = history.entries(limit=3)
        assert len(recent) == 3
        assert recent[0]["filename"] == "f9.bin"

    def test_max_entries_truncates_old(self, tmp_path):
        """History should auto-truncate beyond max_entries to prevent unbounded growth."""
        path = tmp_path / "history.jsonl"
        history = TransferHistory(path, max_entries=5)
        for i in range(10):
            job = _make_job(name=f"f{i}.bin")
            job.finished_at = float(i)
            history.record(job)

        entries = history.entries()
        assert len(entries) <= 5
        # Should keep the most recent
        filenames = [e["filename"] for e in entries]
        assert "f9.bin" in filenames
