"""Convert embedded PNG cover art to JPEG."""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class CoverResult:
    converted: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def iter_audio_files(root: Path) -> Iterator[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if name.lower().endswith((".mp3", ".flac")):
                yield Path(dirpath) / name


def _is_png(mime: str | None, data: bytes) -> bool:
    return mime == "image/png" or b"PNG" in data[:10]


def _to_jpeg(image: Image.Image, quality: int) -> tuple[bytes, int, int]:
    """Convert any embedded image to baseline JPEG, flattening transparency."""
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, subsampling=0)
    return buffer.getvalue(), image.width, image.height


def _convert_mp3(file_path: Path, quality: int) -> tuple[bool, list[str]]:
    messages: list[str] = []
    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        return False, messages

    modified = False
    for key in list(tags.keys()):
        if not key.startswith("APIC:"):
            continue

        apic = tags[key]
        if not _is_png(apic.mime, apic.data):
            continue

        messages.append(f"[MP3] Found PNG artwork in: {file_path.name}")
        with Image.open(io.BytesIO(apic.data)) as image:
            image.load()
            jpeg_data, width, height = _to_jpeg(image, quality)

        apic.mime = "image/jpeg"
        apic.data = jpeg_data
        apic.width = width
        apic.height = height
        modified = True

    if modified:
        tags.save(file_path)
        messages.append(
            f"[MP3] Converted artwork to JPEG: {file_path.name}"
        )
    return modified, messages


def _convert_flac(file_path: Path, quality: int) -> tuple[bool, list[str]]:
    messages: list[str] = []
    audio = FLAC(file_path)
    if not audio.pictures:
        return False, messages

    modified = False
    new_pictures: list[Picture] = []

    for picture in audio.pictures:
        if not _is_png(picture.mime, picture.data):
            new_pictures.append(picture)
            continue

        messages.append(f"[FLAC] Found PNG artwork in: {file_path.name}")
        with Image.open(io.BytesIO(picture.data)) as image:
            image.load()
            jpeg_data, width, height = _to_jpeg(image, quality)

        converted = Picture()
        converted.type = picture.type
        converted.desc = picture.desc
        converted.mime = "image/jpeg"
        converted.width = width
        converted.height = height
        converted.depth = 24
        converted.data = jpeg_data
        new_pictures.append(converted)
        modified = True

    if modified:
        audio.clear_pictures()
        for picture in new_pictures:
            audio.add_picture(picture)
        audio.save()
        messages.append(
            f"[FLAC] Converted artwork to JPEG: {file_path.name}"
        )
    return modified, messages


def convert_covers(
    root: Path,
    quality: int = 100,
    on_log: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel=None,
) -> CoverResult:
    files = list(iter_audio_files(root))
    total = len(files)
    result = CoverResult()

    for index, file_path in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break

        try:
            if file_path.suffix.lower() == ".mp3":
                modified, messages = _convert_mp3(file_path, quality)
            else:
                modified, messages = _convert_flac(file_path, quality)
            if modified:
                result.converted += 1
            else:
                result.unchanged += 1
            if on_log is not None:
                for message in messages:
                    on_log(message)
        except Exception as exc:
            logger.exception("Failed to convert artwork in %s", file_path)
            result.errors.append(f"{file_path.name}: {exc}")
            if on_log is not None:
                on_log(f"Error processing {file_path.name}: {exc}")

        if on_progress is not None:
            on_progress(index, total)

    return result
