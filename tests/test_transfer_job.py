"""
Tests for TransferJob dataclass — properties, defaults, and invariants.

Covers: filename (UPLOAD uses local_path, DOWNLOAD uses remote_path),
        progress (0.0 guard, normal, capped at 1.0), id uniqueness,
        state default, created_at set at construction, field defaults.
"""
from __future__ import annotations

import time
import uuid

import pytest

from sftp_ui.core.transfer import (
    TransferDirection,
    TransferJob,
    TransferState,
)


# ── filename property ──────────────────────────────────────────────────────────

class TestTransferJobFilename:
    def test_upload_filename_from_local_path(self):
        job = TransferJob(
            local_path="/home/user/documents/report.pdf",
            remote_path="/uploads/report.pdf",
            direction=TransferDirection.UPLOAD,
        )
        assert job.filename == "report.pdf"

    def test_download_filename_from_remote_path(self):
        job = TransferJob(
            local_path="/tmp/download.bin",
            remote_path="/server/data/archive.tar.gz",
            direction=TransferDirection.DOWNLOAD,
        )
        assert job.filename == "archive.tar.gz"

    def test_upload_uses_local_basename_not_remote(self):
        job = TransferJob(
            local_path="/local/myfile.csv",
            remote_path="/remote/different_name.csv",
            direction=TransferDirection.UPLOAD,
        )
        assert job.filename == "myfile.csv"

    def test_download_uses_remote_basename_not_local(self):
        job = TransferJob(
            local_path="/local/local_name.bin",
            remote_path="/remote/server_name.bin",
            direction=TransferDirection.DOWNLOAD,
        )
        assert job.filename == "server_name.bin"

    def test_filename_with_nested_remote_path(self):
        job = TransferJob(
            local_path="/tmp/f.txt",
            remote_path="/a/b/c/d/deep.txt",
            direction=TransferDirection.DOWNLOAD,
        )
        assert job.filename == "deep.txt"

    def test_filename_with_nested_local_path(self):
        job = TransferJob(
            local_path="/home/user/projects/my_app/src/main.py",
            remote_path="/r/main.py",
            direction=TransferDirection.UPLOAD,
        )
        assert job.filename == "main.py"

    def test_filename_dotfile(self):
        job = TransferJob(
            local_path="/home/user/.bashrc",
            remote_path="/r/.bashrc",
            direction=TransferDirection.UPLOAD,
        )
        assert job.filename == ".bashrc"

    def test_filename_double_extension(self):
        job = TransferJob(
            local_path="/tmp/archive.tar.gz",
            remote_path="/r/x",
            direction=TransferDirection.UPLOAD,
        )
        assert job.filename == "archive.tar.gz"

    def test_default_direction_is_upload(self):
        job = TransferJob(local_path="/local/f.txt", remote_path="/r/f.txt")
        assert job.direction == TransferDirection.UPLOAD
        assert job.filename == "f.txt"  # uses local_path


# ── progress property ──────────────────────────────────────────────────────────

class TestTransferJobProgress:
    def test_zero_total_bytes_returns_zero(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 0
        job.bytes_done = 0
        assert job.progress == 0.0

    def test_zero_bytes_done_returns_zero(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 1000
        job.bytes_done = 0
        assert job.progress == 0.0

    def test_half_done(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 1000
        job.bytes_done = 500
        assert job.progress == 0.5

    def test_fully_done(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 1000
        job.bytes_done = 1000
        assert job.progress == 1.0

    def test_capped_at_one_if_over(self):
        """bytes_done > total_bytes should never give progress > 1.0."""
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 100
        job.bytes_done = 150
        assert job.progress == 1.0

    def test_progress_one_quarter(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 400
        job.bytes_done = 100
        assert job.progress == 0.25

    def test_progress_returns_float(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        assert isinstance(job.progress, float)

    def test_total_zero_does_not_raise(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        job.total_bytes = 0
        job.bytes_done = 999
        # Must not raise ZeroDivisionError
        _ = job.progress


# ── defaults and invariants ────────────────────────────────────────────────────

class TestTransferJobDefaults:
    def test_default_state_is_pending(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        assert job.state == TransferState.PENDING

    def test_default_bytes_done_zero(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        assert job.bytes_done == 0

    def test_default_total_bytes_zero(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        assert job.total_bytes == 0

    def test_default_error_is_none(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        assert job.error is None

    def test_default_finished_at_is_none(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        assert job.finished_at is None

    def test_created_at_set_on_construction(self):
        before = time.time()
        job = TransferJob(local_path="/f", remote_path="/r")
        after = time.time()
        assert before <= job.created_at <= after

    def test_id_is_valid_uuid(self):
        job = TransferJob(local_path="/f", remote_path="/r")
        uuid.UUID(job.id)  # raises if not a valid UUID

    def test_ids_are_unique(self):
        jobs = [TransferJob(local_path="/f", remote_path="/r") for _ in range(10)]
        ids = {j.id for j in jobs}
        assert len(ids) == 10

    def test_custom_id_accepted(self):
        custom = "my-custom-id-123"
        job = TransferJob(local_path="/f", remote_path="/r", id=custom)
        assert job.id == custom
