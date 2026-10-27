"""Reading metadata, artwork and folder structure from audio files."""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from PIL import Image

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".flac", ".mp3"}


def is_audio_file(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in AUDIO_EXTENSIONS
    )


def has_audio(folder: Path) -> bool:
    try:
        return any(is_audio_file(entry) for entry in folder.iterdir())
    except OSError:
        return False


def album_directories(root: Path, include_root: bool = False) -> list[Path]:
    """Return the folders that look like albums under *root*.

    The playlist organizer treats subfolders as albums.  The downsampler can
    also treat the selected folder itself as an album.
    """
    root = Path(root)
    albums: list[Path] = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return albums

    if include_root and has_audio(root):
        albums.append(root)

    for entry in entries:
        if entry.is_dir() and has_audio(entry):
            albums.append(entry)
    return albums


def list_audio_files(folder: Path) -> list[Path]:
    try:
        entries = folder.iterdir()
    except OSError:
        return []
    return sorted(
        (entry for entry in entries if is_audio_file(entry)),
        key=lambda entry: entry.name.lower(),
    )


def _first(values) -> str:
    if not values:
        return ""
    return str(values[0]).strip()


def _frame_text(tags: ID3, key: str) -> str:
    frame = tags.get(key)
    if frame is None:
        return ""
    try:
        return str(frame.text[0]).strip()
    except (AttributeError, IndexError):
        return ""


def read_track_metadata(file_path: Path) -> dict[str, str]:
    """Read track number, title and album from a FLAC or MP3 file."""
    file_path = Path(file_path)
    meta = {"track": "", "title": file_path.stem, "album": ""}

    try:
        if file_path.suffix.lower() == ".flac":
            audio = FLAC(file_path)
            meta = {
                "track": _first(audio.get("tracknumber")),
                "title": _first(audio.get("title")) or file_path.stem,
                "album": _first(audio.get("album")),
            }
        elif file_path.suffix.lower() == ".mp3":
            tags = ID3(file_path)
            meta = {
                "track": _frame_text(tags, "TRCK"),
                "title": _frame_text(tags, "TIT2") or file_path.stem,
                "album": _frame_text(tags, "TALB"),
            }
    except ID3NoHeaderError:
        pass
    except Exception:
        logger.debug("Could not read metadata from %s", file_path, exc_info=True)

    meta["track"] = meta["track"].split("/")[0].strip()
    if not meta["title"]:
        meta["title"] = file_path.stem
    return meta


def track_sort_key(entry: tuple[Path, dict[str, str]]):
    file_path, meta = entry
    track = meta["track"]
    if track:
        try:
            return (0, int(track), file_path.name.lower())
        except ValueError:
            return (1, track, file_path.name.lower())
    return (2, "", file_path.name.lower())


class MetadataCache:
    """mtime-keyed metadata cache that is safe to use from worker threads."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[int, dict[str, str]]] = {}
        self._lock = threading.Lock()

    def get(self, file_path: Path) -> dict[str, str]:
        file_path = Path(file_path)
        key = str(file_path)
        try:
            mtime = file_path.stat().st_mtime_ns
        except OSError:
            mtime = 0

        with self._lock:
            cached = self._entries.get(key)
        if cached is not None and cached[0] == mtime:
            return dict(cached[1])

        meta = read_track_metadata(file_path)
        with self._lock:
            self._entries[key] = (mtime, meta)
        return dict(meta)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def read_flac_info(file_path: Path) -> tuple[int | None, int | None]:
    try:
        info = FLAC(file_path).info
        return info.bits_per_sample, info.sample_rate
    except Exception:
        return None, None


def format_file_label(file_path: Path) -> tuple[str, str]:
    """Return a (name, detail) pair for lists."""
    file_path = Path(file_path)
    if file_path.suffix.lower() == ".flac":
        bits, rate = read_flac_info(file_path)
        if bits and rate:
            return file_path.name, f"{bits}-bit / {rate / 1000:g} kHz"
        if bits:
            return file_path.name, f"{bits}-bit"
        return file_path.name, "FLAC"
    return file_path.name, "MP3 · lossy"


def extract_art(file_path: Path) -> Image.Image | None:
    """Return the first embedded picture, fully loaded into memory."""
    file_path = Path(file_path)
    try:
        if file_path.suffix.lower() == ".flac":
            audio = FLAC(file_path)
            if audio.pictures:
                image = Image.open(io.BytesIO(audio.pictures[0].data))
                image.load()
                return image
        elif file_path.suffix.lower() == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                return None
            for key in tags.keys():
                if key.startswith("APIC:"):
                    image = Image.open(io.BytesIO(tags[key].data))
                    image.load()
                    return image
    except Exception:
        logger.debug("Could not extract artwork from %s", file_path, exc_info=True)
    return None
