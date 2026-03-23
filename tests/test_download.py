"""
Tests for TransferEngine.download() — the counterpart to upload.

Covers: clean download, resume, skip-if-done, local-larger-restart,
progress, cancellation, error handling, directory creation, retry.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sftp_ui.core.transfer import (
    CHUNK_SIZE,
    TransferDirection,
    TransferEngine,
    TransferError,
    TransferJob,
    TransferState,
)
from tests.conftest import FakeSFTPClient


def make_engine(sftp=None, chunk_size=CHUNK_SIZE):
    return TransferEngine(sftp or FakeSFTPClient(), chunk_size=chunk_size)


def make_job(remote_path: str = "/remote/file.bin", local_path: str = "") -> TransferJob:
    return TransferJob(
        local_path=local_path,
        remote_path=remote_path,
        direction=TransferDirection.DOWNLOAD,
    )


# ── Clean download ─────────────────────────────────────────────────────────────

class TestCleanDownload:
    def test_downloads_full_content(self, tmp_path):
        content = os.urandom(1024)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        engine = make_engine(sftp)
        local = str(tmp_path / "f.bin")
        job = make_job("/r/f.bin", local)

        engine.download(job)

        assert Path(local).read_bytes() == content
        assert job.state == TransferState.DONE
        assert job.bytes_done == len(content)
        assert job.total_bytes == len(content)

    def test_state_becomes_done(self, tmp_path):
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(b"hi")})
        local = str(tmp_path / "f.bin")
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", local)
        engine.download(job)
        assert job.state == TransferState.DONE

    def test_finished_at_set(self, tmp_path):
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(b"x")})
        local = str(tmp_path / "f.bin")
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", local)
        before = time.time()
        engine.download(job)
        assert job.finished_at is not None
        assert job.finished_at >= before

    def test_multi_chunk_download(self, tmp_path):
        chunk = 256
        content = os.urandom(chunk * 5 + 17)
        sftp = FakeSFTPClient(remote_files={"/r/big.bin": bytearray(content)})
        local = str(tmp_path / "big.bin")
        engine = make_engine(sftp, chunk_size=chunk)
        job = make_job("/r/big.bin", local)

        engine.download(job)

        assert Path(local).read_bytes() == content

    def test_creates_parent_directories(self, tmp_path):
        content = b"nested"
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "deep" / "nested" / "f.bin")
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", local)

        engine.download(job)

        assert Path(local).read_bytes() == content

    def test_filename_property_uses_remote_basename(self, tmp_path):
        sftp = FakeSFTPClient(remote_files={"/reports/q4.csv": bytearray(b"data")})
        job = make_job("/reports/q4.csv", str(tmp_path / "q4.csv"))
        assert job.filename == "q4.csv"


# ── Skip if already complete ───────────────────────────────────────────────────

class TestDownloadSkip:
    def test_skip_if_local_matches_remote(self, tmp_path):
        content = os.urandom(512)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = tmp_path / "f.bin"
        local.write_bytes(content)   # already complete
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", str(local))

        engine.download(job)

        assert job.state == TransferState.DONE
        assert job.bytes_done == len(content)

    def test_skip_calls_progress_once(self, tmp_path):
        content = os.urandom(256)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = tmp_path / "f.bin"
        local.write_bytes(content)
        calls = []
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", str(local))

        engine.download(job, progress_callback=lambda d, t: calls.append((d, t)))

        assert calls == [(len(content), len(content))]


# ── Resume ─────────────────────────────────────────────────────────────────────

class TestDownloadResume:
    def test_resumes_from_partial_local(self, tmp_path):
        content = os.urandom(400)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = tmp_path / "f.bin"
        local.write_bytes(content[:200])   # first half already local
        engine = make_engine(sftp, chunk_size=100)
        job = make_job("/r/f.bin", str(local))

        engine.download(job)

        assert local.read_bytes() == content
        assert job.state == TransferState.DONE

    def test_local_larger_than_remote_restarts(self, tmp_path):
        content = os.urandom(256)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = tmp_path / "f.bin"
        local.write_bytes(b"X" * 1024)   # local is bigger — corrupted
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", str(local))

        engine.download(job)

        assert local.read_bytes() == content
        assert job.state == TransferState.DONE


# ── Progress ───────────────────────────────────────────────────────────────────

class TestDownloadProgress:
    def test_progress_called_at_least_once(self, tmp_path):
        content = os.urandom(1024)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "f.bin")
        calls = []
        engine = make_engine(sftp, chunk_size=256)
        job = make_job("/r/f.bin", local)

        engine.download(job, progress_callback=lambda d, t: calls.append((d, t)))

        assert len(calls) > 0

    def test_progress_final_equals_total(self, tmp_path):
        content = os.urandom(1024)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "f.bin")
        calls = []
        engine = make_engine(sftp, chunk_size=256)
        job = make_job("/r/f.bin", local)

        engine.download(job, progress_callback=lambda d, t: calls.append((d, t)))

        last_done, last_total = calls[-1]
        assert last_total == len(content)
        assert last_done == len(content)

    def test_progress_monotonically_increasing(self, tmp_path):
        content = os.urandom(2048)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "f.bin")
        calls = []
        engine = make_engine(sftp, chunk_size=256)
        job = make_job("/r/f.bin", local)

        engine.download(job, progress_callback=lambda d, t: calls.append(d))

        for a, b in zip(calls, calls[1:]):
            assert b >= a


# ── Cancellation ───────────────────────────────────────────────────────────────

class TestDownloadCancellation:
    def test_cancel_before_start(self, tmp_path):
        content = os.urandom(4096)
        sftp = FakeSFTPClient(remote_files={"/r/big.bin": bytearray(content)})
        local = str(tmp_path / "big.bin")
        engine = make_engine(sftp, chunk_size=256)
        job = make_job("/r/big.bin", local)

        engine.download(job, cancel_flag=lambda: True)

        assert job.state == TransferState.CANCELLED

    def test_cancel_mid_download(self, tmp_path):
        content = os.urandom(2048)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "f.bin")
        engine = make_engine(sftp, chunk_size=256)
        job = make_job("/r/f.bin", local)
        call_count = [0]

        def cancel_after_2():
            call_count[0] += 1
            return call_count[0] > 2

        engine.download(job, cancel_flag=cancel_after_2)

        assert job.state == TransferState.CANCELLED
        assert job.bytes_done < len(content)


# ── Error handling ─────────────────────────────────────────────────────────────

class TestDownloadErrors:
    def test_missing_remote_file_raises(self, tmp_path):
        sftp = FakeSFTPClient()   # empty — no files
        local = str(tmp_path / "f.bin")
        engine = make_engine(sftp)
        job = make_job("/r/nope.bin", local)

        with pytest.raises(TransferError, match="stat"):
            engine.download(job)

        assert job.state == TransferState.FAILED

    def test_read_error_raises_transfer_error(self, tmp_path):
        content = os.urandom(512)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "f.bin")
        original_open = sftp.open_remote

        def broken_open(path, mode):
            raise OSError("network cut")

        sftp.open_remote = broken_open
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", local)

        with pytest.raises(TransferError):
            engine.download(job)

        assert job.state == TransferState.FAILED

    def test_failed_job_has_error_message(self, tmp_path):
        sftp = FakeSFTPClient()
        local = str(tmp_path / "f.bin")
        engine = make_engine(sftp)
        job = make_job("/r/nope.bin", local)

        try:
            engine.download(job)
        except TransferError:
            pass

        assert job.error is not None and len(job.error) > 0


# ── Retry ──────────────────────────────────────────────────────────────────────

class TestDownloadRetry:
    def test_succeeds_on_second_attempt(self, tmp_path):
        content = os.urandom(256)
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(content)})
        local = str(tmp_path / "f.bin")
        attempt = [0]
        original_open = sftp.open_remote

        def flaky_open(p, mode):
            attempt[0] += 1
            if attempt[0] == 1:
                raise OSError("transient")
            return original_open(p, mode)

        sftp.open_remote = flaky_open
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", local)

        engine.download_with_retry(job, max_retries=3, retry_delay=0)

        assert job.state == TransferState.DONE
        assert Path(local).read_bytes() == content

    def test_fails_after_max_retries(self, tmp_path):
        sftp = FakeSFTPClient(remote_files={"/r/f.bin": bytearray(b"x")})
        local = str(tmp_path / "f.bin")

        def always_fail(p, mode):
            raise OSError("broken")

        sftp.open_remote = always_fail
        engine = make_engine(sftp)
        job = make_job("/r/f.bin", local)

        with pytest.raises(TransferError):
            engine.download_with_retry(job, max_retries=3, retry_delay=0)

        assert job.state == TransferState.FAILED


# ── Binary integrity ───────────────────────────────────────────────────────────

class TestDownloadBinaryIntegrity:
    def test_all_byte_values_preserved(self, tmp_path):
        """Every byte value 0x00–0xFF must survive the roundtrip."""
        content = bytes(range(256)) * 16    # 4 KB with all possible bytes
        sftp = FakeSFTPClient(remote_files={"/r/all_bytes.bin": bytearray(content)})
        local = str(tmp_path / "all_bytes.bin")
        engine = make_engine(sftp, chunk_size=64)
        job = make_job("/r/all_bytes.bin", local)

        engine.download(job)

        assert Path(local).read_bytes() == content

    def test_null_bytes_in_content(self, tmp_path):
        content = b"\x00" * 1024
        sftp = FakeSFTPClient(remote_files={"/r/nulls.bin": bytearray(content)})
        local = str(tmp_path / "nulls.bin")
        engine = make_engine(sftp)
        job = make_job("/r/nulls.bin", local)

        engine.download(job)

        assert Path(local).read_bytes() == content
