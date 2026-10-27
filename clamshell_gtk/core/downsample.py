"""Downsample high-resolution FLAC files to 16-bit, safely and verifiably."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.flac import FLAC

logger = logging.getLogger(__name__)

FLAC_APPLICATION = 2
FLAC_VORBIS_COMMENT = 4
FLAC_CUESHEET = 5
FLAC_PICTURE = 6
PRESERVED_BLOCK_TYPES = (
    FLAC_APPLICATION,
    FLAC_VORBIS_COMMENT,
    FLAC_CUESHEET,
    FLAC_PICTURE,
)

TEMP_PREFIX = ".clamshell-"
TEMP_SUFFIX = ".part.flac"
FLAC_BLOCK_SIZE = 4608

CANCELLED = "__cancelled__"


@dataclass
class DownsampleSummary:
    converted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def read_flac_metadata(path: Path) -> tuple[list[tuple[int, bytes]], int]:
    blocks: list[tuple[int, bytes]] = []
    with open(path, "rb") as handle:
        if handle.read(4) != b"fLaC":
            raise ValueError("not a FLAC file")
        while True:
            header = handle.read(4)
            if len(header) != 4:
                raise ValueError("truncated FLAC metadata")
            last = header[0] & 0x80
            block_type = header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            data = handle.read(length)
            if len(data) != length:
                raise ValueError("truncated FLAC metadata block")
            blocks.append((block_type, data))
            if last:
                break
        audio_offset = handle.tell()
    return blocks, audio_offset


def preserve_extra_blocks(original: Path, converted: Path) -> bool:
    """Re-inject tags, cuesheets and pictures that ffmpeg did not keep."""
    original_blocks, _ = read_flac_metadata(original)
    extra = [
        (block_type, data)
        for block_type, data in original_blocks
        if block_type in PRESERVED_BLOCK_TYPES
    ]
    if not extra:
        return False

    converted_blocks, audio_offset = read_flac_metadata(converted)
    converted_blocks = [
        (block_type, data)
        for block_type, data in converted_blocks
        if block_type not in PRESERVED_BLOCK_TYPES
    ]
    new_blocks = converted_blocks[:1] + extra + converted_blocks[1:]

    meta_path = converted.with_name(converted.name + ".meta")
    with open(meta_path, "wb") as output:
        output.write(b"fLaC")
        for index, (block_type, data) in enumerate(new_blocks):
            last = 0x80 if index == len(new_blocks) - 1 else 0x00
            output.write(bytes([last | block_type]))
            output.write(len(data).to_bytes(3, "big"))
            output.write(data)
        with open(converted, "rb") as source:
            source.seek(audio_offset)
            shutil.copyfileobj(source, output, length=1024 * 1024)

    os.replace(meta_path, converted)
    return True


class DownsampleJob:
    """Convert >16-bit FLACs to 16-bit with validation before replacing."""

    def __init__(self, ffmpeg: str = "ffmpeg", max_workers: int | None = None):
        self.ffmpeg = ffmpeg
        self.flac_bin = shutil.which("flac")
        if max_workers is None:
            max_workers = min(8, os.cpu_count() or 1)
        self.max_workers = max(1, max_workers)

    def run(
        self,
        file_paths: Iterable[Path],
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        cancel=None,
    ) -> DownsampleSummary:
        paths = list(dict.fromkeys(Path(path) for path in file_paths))
        total = len(paths)
        summary = DownsampleSummary()

        def log(message: str) -> None:
            if on_log is not None:
                on_log(message)

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        )
        futures = {
            executor.submit(self._process_file, path, cancel): path
            for path in paths
        }
        completed = 0

        try:
            for future in concurrent.futures.as_completed(futures):
                path = futures[future]
                if cancel is not None and cancel.is_set():
                    summary.cancelled = True
                    for pending in futures:
                        pending.cancel()
                    break

                try:
                    status, message = future.result()
                except Exception as exc:
                    logger.exception("Failed to downsample %s", path)
                    status = "skipped"
                    message = f"Skipped (kept original): {path.name} - {exc}"

                if status == "cancelled":
                    summary.cancelled = True
                    continue
                if status == "converted":
                    summary.converted += 1
                else:
                    summary.skipped += 1
                    if status == "error":
                        summary.errors.append(message or path.name)

                if message:
                    log(message)

                completed += 1
                if on_progress is not None:
                    on_progress(completed, total)
        finally:
            if cancel is not None and cancel.is_set():
                summary.cancelled = True
            executor.shutdown(wait=True, cancel_futures=True)

        return summary

    def _process_file(self, file_path: Path, cancel) -> tuple[str, str | None]:
        if cancel is not None and cancel.is_set():
            return "cancelled", None

        try:
            bits = FLAC(file_path).info.bits_per_sample
        except Exception:
            return "skipped", f"Skipped (not lossless FLAC): {file_path.name}"
        if bits is None:
            return "skipped", f"Skipped (not lossless FLAC): {file_path.name}"
        if bits <= 16:
            return "skipped", f"Skipped (already {bits}-bit): {file_path.name}"

        ok, reason = self.convert_file(file_path, cancel)
        if reason == CANCELLED:
            return "cancelled", None
        if ok:
            return "converted", None
        return (
            "error",
            f"Skipped (kept original): {file_path.name} - {reason}",
        )

    def _wait(self, process: subprocess.Popen, cancel) -> int:
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                return process.returncode
            time.sleep(0.1)
        return process.returncode

    def _run_capture(self, command: list[str], cancel) -> tuple[int, str, bool]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        code = self._wait(process, cancel)
        _, stderr = process.communicate()
        cancelled = cancel is not None and cancel.is_set()
        return code, stderr or "", cancelled

    def _encode_ffmpeg(self, file_path: Path, tmp_path: Path, cancel) -> str | None:
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(file_path),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "flac",
            "-sample_fmt",
            "s16",
            "-frame_size",
            str(FLAC_BLOCK_SIZE),
            "-af",
            "aresample=dither_method=triangular",
            str(tmp_path),
        ]
        code, stderr, cancelled = self._run_capture(command, cancel)
        if cancelled:
            return CANCELLED
        if code != 0:
            return stderr.strip()[:200] or "ffmpeg failed"
        return None

    def _encode_reference_decoder(
        self, file_path: Path, tmp_path: Path, cancel
    ) -> str | None:
        if not self.flac_bin:
            return "reference flac decoder not found"

        decode = subprocess.Popen(
            [self.flac_bin, "-d", "-c", "--silent", str(file_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        encode = subprocess.Popen(
            [
                self.ffmpeg,
                "-hide_banner",
                "-v",
                "error",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-c:a",
                "flac",
                "-sample_fmt",
                "s16",
                "-frame_size",
                str(FLAC_BLOCK_SIZE),
                "-af",
                "aresample=dither_method=triangular",
                str(tmp_path),
            ],
            stdin=decode.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if decode.stdout is not None:
            decode.stdout.close()

        code = self._wait(encode, cancel)
        cancelled = cancel is not None and cancel.is_set()
        if cancelled:
            decode.terminate()
        _, stderr = encode.communicate()
        decode.wait()

        if cancelled:
            return CANCELLED
        if decode.returncode != 0:
            return (
                "reference decoder could not read the source "
                "(source may be corrupt)"
            )
        if code != 0:
            return stderr.strip()[:200] or "ffmpeg failed"
        return None

    def convert_file(self, file_path: Path, cancel=None) -> tuple[bool, str]:
        try:
            original_info = FLAC(file_path).info
        except Exception as exc:
            return False, f"could not read original ({exc})"

        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(file_path.parent),
            prefix=TEMP_PREFIX,
            suffix=TEMP_SUFFIX,
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        try:
            original_stat = file_path.stat()
            attempts = [("ffmpeg", self._encode_ffmpeg)]
            if self.flac_bin:
                attempts.append(
                    ("reference decoder", self._encode_reference_decoder)
                )

            reasons: list[str] = []
            for name, encoder in attempts:
                if cancel is not None and cancel.is_set():
                    return False, CANCELLED
                error = encoder(file_path, tmp_path, cancel)
                if error == CANCELLED:
                    return False, CANCELLED
                if error is not None:
                    reasons.append(f"{name}: {error}")
                    continue

                try:
                    preserve_extra_blocks(file_path, tmp_path)
                except Exception as exc:
                    reasons.append(f"{name}: metadata restore failed ({exc})")
                    continue

                ok, reason = self.validate(tmp_path, original_info, cancel)
                if reason == CANCELLED:
                    return False, CANCELLED
                if not ok:
                    reasons.append(f"{name}: {reason}")
                    continue

                self.apply_original_stat(tmp_path, original_stat)
                os.replace(tmp_path, file_path)
                return True, ""

            return (
                False,
                "could not convert source (" + "; ".join(reasons) + ")",
            )
        except Exception as exc:
            logger.exception("Downsampling failed for %s", file_path)
            return False, str(exc)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def validate(self, path: Path, original_info, cancel=None) -> tuple[bool, str]:
        if cancel is not None and cancel.is_set():
            return False, CANCELLED
        try:
            info = FLAC(path).info
        except Exception as exc:
            return False, f"output unreadable ({exc})"

        if info.bits_per_sample != 16:
            return False, f"output is {info.bits_per_sample}-bit"
        if info.channels != original_info.channels:
            return False, "channel count changed"
        if info.sample_rate != original_info.sample_rate:
            return False, "sample rate changed"
        if info.total_samples != original_info.total_samples:
            return (
                False,
                f"sample count mismatch ({info.total_samples} vs "
                f"{original_info.total_samples})",
            )

        if self.flac_bin:
            command = [self.flac_bin, "-t", "--silent", str(path)]
        else:
            command = [
                self.ffmpeg,
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ]

        code, stderr, cancelled = self._run_capture(command, cancel)
        if cancelled:
            return False, CANCELLED
        if code != 0:
            return False, f"decode check failed ({stderr.strip()[:200]})"
        return True, ""

    @staticmethod
    def apply_original_stat(path: Path, original_stat) -> None:
        try:
            os.chmod(path, stat.S_IMODE(original_stat.st_mode))
        except OSError:
            pass
        try:
            os.chown(path, original_stat.st_uid, original_stat.st_gid)
        except (OSError, AttributeError):
            pass
        try:
            os.utime(
                path,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
        except (OSError, AttributeError):
            pass
