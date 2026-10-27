"""Playlist organization: tag writing, renaming and copying tracks."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TRCK

from .audio import read_track_metadata

logger = logging.getLogger(__name__)

INVALID_FILENAME_CHARS = '<>:"/\\|?*'
MAX_STEM_LENGTH = 120


@dataclass
class ApplyResult:
    applied: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


@dataclass
class CopyResult:
    copied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def sanitize_filename(name: str) -> str:
    cleaned = "".join(
        char
        for char in name
        if char not in INVALID_FILENAME_CHARS and ord(char) >= 32
    )
    cleaned = cleaned.strip().strip(".")
    return cleaned or "Untitled"


def unique_path(path: Path) -> Path:
    """Return *path*, or an unused ``name (2).ext`` variant of it."""
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _write_tags(file_path: Path, track_number: str, album_title: str | None) -> None:
    if file_path.suffix.lower() == ".flac":
        audio = FLAC(file_path)
        audio["tracknumber"] = track_number
        if album_title:
            audio["album"] = album_title
        audio.save()
        return

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags["TRCK"] = TRCK(encoding=3, text=track_number)
    if album_title:
        tags["TALB"] = TALB(encoding=3, text=album_title)
    tags.save(file_path)


def _rename_track(file_path: Path, track_number: str) -> Path:
    """Rename a file to ``NN - Title.ext`` without ever overwriting a file."""
    title = read_track_metadata(file_path)["title"]
    stem = sanitize_filename(f"{track_number} - {title}")[:MAX_STEM_LENGTH].rstrip()
    target = file_path.with_name(f"{stem}{file_path.suffix}")

    if target == file_path:
        return file_path

    if target.exists():
        try:
            if target.samefile(file_path):
                return file_path
        except OSError:
            pass
        target = unique_path(target)

    file_path.rename(target)
    return target


def apply_album(
    file_paths: Iterable[Path],
    album_title: str,
    on_progress: Callable[[int, int], None] | None = None,
    on_item: Callable[[int, str, str], None] | None = None,
    cancel=None,
) -> ApplyResult:
    """Renumber tracks in list order, rewrite tags and rename files."""
    paths = [Path(path) for path in file_paths]
    total = len(paths)
    width = max(2, len(str(total)))
    album_title = album_title.strip()
    result = ApplyResult()

    for index, file_path in enumerate(paths):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break

        track_number = str(index + 1).zfill(width)
        try:
            _write_tags(file_path, track_number, album_title)
            new_path = _rename_track(file_path, track_number)
            new_title = read_track_metadata(new_path)["title"]
            result.applied += 1
            if on_item is not None:
                on_item(index, new_title, str(new_path))
        except Exception as exc:
            logger.exception("Failed to update %s", file_path)
            result.errors.append(f"{file_path.name}: {exc}")

        if on_progress is not None:
            on_progress(index + 1, total)

    return result


def add_to_playlist(
    sources: Iterable[Path],
    target_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
    cancel=None,
) -> CopyResult:
    """Copy tracks into *target_dir*, skipping names that already exist."""
    target_dir = Path(target_dir)
    source_paths = [Path(source) for source in sources]
    total = len(source_paths)
    result = CopyResult()

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"Could not create {target_dir.name}: {exc}")
        return result

    for index, source in enumerate(source_paths):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break

        destination = target_dir / source.name
        if destination.exists():
            result.skipped += 1
        else:
            try:
                shutil.copy2(source, destination)
                result.copied += 1
            except OSError as exc:
                logger.exception("Failed to copy %s", source)
                result.errors.append(f"{source.name}: {exc}")

        if on_progress is not None:
            on_progress(index + 1, total)

    return result
