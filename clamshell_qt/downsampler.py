import sys
import os
import shutil
import stat
import subprocess
import tempfile
import concurrent.futures
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QTextEdit,
)
from PySide6.QtCore import Qt, QThread, Signal
from mutagen.flac import FLAC


AUDIO_EXTENSIONS = {".flac", ".mp3"}

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


def get_audio_info(file_path):
    try:
        if file_path.suffix.lower() == ".flac":
            info = FLAC(file_path).info
            return info.bits_per_sample, info.sample_rate
    except Exception:
        pass
    return None, None


def format_file_label(file_path):
    bits, rate = get_audio_info(file_path)
    if bits and rate:
        return f"{file_path.name}  ({bits}-bit / {rate / 1000:g} kHz)"
    if bits:
        return f"{file_path.name}  ({bits}-bit)"
    return f"{file_path.name}  (lossy / no bit depth)"


def is_audio_file(path):
    return (
        path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in AUDIO_EXTENSIONS
    )


def find_albums(root):
    root = Path(root)
    albums = []

    def has_audio(folder):
        try:
            return any(is_audio_file(f) for f in folder.iterdir())
        except OSError:
            return False

    if has_audio(root):
        albums.append(root)
    for subdir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if subdir.is_dir() and has_audio(subdir):
            albums.append(subdir)
    return albums


def list_audio_files(folder):
    folder = Path(folder)
    return sorted(
        (f for f in folder.iterdir() if is_audio_file(f)),
        key=lambda p: p.name.lower(),
    )


def read_flac_metadata(path):
    blocks = []
    with open(path, "rb") as f:
        if f.read(4) != b"fLaC":
            raise ValueError("not a FLAC file")
        while True:
            header = f.read(4)
            if len(header) != 4:
                raise ValueError("truncated FLAC metadata")
            last = header[0] & 0x80
            block_type = header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            data = f.read(length)
            if len(data) != length:
                raise ValueError("truncated FLAC metadata block")
            blocks.append((block_type, data))
            if last:
                break
        audio_offset = f.tell()
    return blocks, audio_offset


def preserve_extra_blocks(original, converted):
    orig_blocks, _ = read_flac_metadata(original)
    extra = [
        (block_type, data)
        for block_type, data in orig_blocks
        if block_type in PRESERVED_BLOCK_TYPES
    ]
    if not extra:
        return False

    conv_blocks, audio_offset = read_flac_metadata(converted)
    conv_blocks = [
        (block_type, data)
        for block_type, data in conv_blocks
        if block_type not in PRESERVED_BLOCK_TYPES
    ]
    new_blocks = conv_blocks[:1] + extra + conv_blocks[1:]

    tmp_path = converted.with_name(converted.name + ".meta")
    with open(tmp_path, "wb") as out:
        out.write(b"fLaC")
        for index, (block_type, data) in enumerate(new_blocks):
            last = 0x80 if index == len(new_blocks) - 1 else 0x00
            out.write(bytes([last | block_type]))
            out.write(len(data).to_bytes(3, "big"))
            out.write(data)
        with open(converted, "rb") as src:
            src.seek(audio_offset)
            shutil.copyfileobj(src, out, length=1024 * 1024)

    os.replace(tmp_path, converted)
    return True


class DownsampleWorker(QThread):
    progress_signal = Signal(int, int)
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, file_paths, ffmpeg="ffmpeg", max_workers=None):
        super().__init__()
        self.file_paths = [Path(p) for p in file_paths]
        self.ffmpeg = ffmpeg
        self.flac_bin = shutil.which("flac")
        self.should_stop = False
        if max_workers is None:
            max_workers = os.cpu_count() or 1
        self.max_workers = max(1, max_workers)

    def _process_file(self, file_path):
        if self.should_stop:
            return None, None

        bits, _ = get_audio_info(file_path)
        if bits is None:
            return "skipped", f"Skipped (not lossless FLAC): {file_path.name}"
        if bits <= 16:
            return "skipped", f"Skipped (already {bits}-bit): {file_path.name}"

        ok, reason = self.convert_file(file_path)
        if ok:
            return "converted", None
        return (
            "skipped",
            f"Skipped (kept original): {file_path.name} - {reason}",
        )

    def run(self):
        total = len(self.file_paths)
        converted = 0
        skipped = 0
        completed = 0

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        )
        futures = {}
        try:
            for file_path in self.file_paths:
                if self.should_stop:
                    break
                futures[executor.submit(self._process_file, file_path)] = (
                    file_path
                )

            for future in concurrent.futures.as_completed(futures):
                file_path = futures[future]
                try:
                    status, message = future.result()
                except Exception as e:
                    status = "skipped"
                    message = (
                        f"Skipped (kept original): {file_path.name} - {e}"
                    )

                if status is None:
                    continue

                if status == "converted":
                    converted += 1
                else:
                    skipped += 1
                if message:
                    self.log_signal.emit(message)

                completed += 1
                self.progress_signal.emit(completed, total)

                if self.should_stop:
                    break
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        self.log_signal.emit(
            f"Finished: {converted} converted, {skipped} skipped."
        )
        self.finished_signal.emit()

    def encode_with_ffmpeg(self, file_path, tmp_path):
        cmd = [
            self.ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(file_path),
            "-map",
            "0:a",
            "-map",
            "0:v?",
            "-c:a",
            "flac",
            "-sample_fmt",
            "s16",
            "-frame_size",
            str(FLAC_BLOCK_SIZE),
            "-c:v",
            "copy",
            "-af",
            "aresample=dither_method=triangular",
            str(tmp_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return result.stderr.strip()[:200] or "ffmpeg failed"
        return None

    def encode_with_reference_decoder(self, file_path, tmp_path):
        if not self.flac_bin:
            return "reference flac decoder not found"

        decode = subprocess.Popen(
            [self.flac_bin, "-d", "-c", "--silent", str(file_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        decode_stdout = decode.stdout
        encode = subprocess.run(
            [
                self.ffmpeg,
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
                str(tmp_path),
            ],
            stdin=decode_stdout,
            capture_output=True,
            text=True,
        )
        if decode_stdout is not None:
            decode_stdout.close()
        decode.wait()

        if decode.returncode != 0:
            return (
                "reference decoder could not read the source "
                "(source may be corrupt)"
            )
        if encode.returncode != 0:
            return encode.stderr.strip()[:200] or "ffmpeg failed"
        return None

    def convert_file(self, file_path):
        try:
            orig_info = FLAC(file_path).info
        except Exception as e:
            return False, f"could not read original ({e})"

        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(file_path.parent),
            prefix=TEMP_PREFIX,
            suffix=TEMP_SUFFIX,
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        try:
            original_stat = file_path.stat()

            attempts = [("ffmpeg", self.encode_with_ffmpeg)]
            if self.flac_bin:
                attempts.append(
                    ("reference decoder", self.encode_with_reference_decoder)
                )

            reasons = []
            for name, encoder in attempts:
                error = encoder(file_path, tmp_path)
                if error is not None:
                    reasons.append(f"{name}: {error}")
                    continue

                preserve_extra_blocks(file_path, tmp_path)

                ok, reason = self.validate(tmp_path, orig_info)
                if not ok:
                    reasons.append(f"{name}: {reason}")
                    continue

                self.apply_original_stat(tmp_path, original_stat)
                os.replace(tmp_path, file_path)
                return True, ""

            return False, "could not convert source (" + "; ".join(reasons) + ")"
        except Exception as e:
            return False, str(e)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def validate(self, path, orig_info):
        try:
            info = FLAC(path).info
        except Exception as e:
            return False, f"output unreadable ({e})"

        if info.bits_per_sample != 16:
            return False, f"output is {info.bits_per_sample}-bit"
        if info.channels != orig_info.channels:
            return False, "channel count changed"
        if info.sample_rate != orig_info.sample_rate:
            return False, "sample rate changed"
        if info.total_samples != orig_info.total_samples:
            return (
                False,
                f"sample count mismatch ({info.total_samples} vs "
                f"{orig_info.total_samples})",
            )

        if self.flac_bin:
            result = subprocess.run(
                [self.flac_bin, "-t", "--silent", str(path)],
                capture_output=True,
                text=True,
            )
        else:
            result = subprocess.run(
                [
                    self.ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode != 0:
            return False, f"decode check failed ({result.stderr.strip()[:200]})"
        return True, ""

    def apply_original_stat(self, path, original_stat):
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

    def stop(self):
        self.should_stop = True


class DownsampleGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Downsampler")

        self.current_album = None
        self.albums = []
        self.worker = None

        main_layout = QVBoxLayout(self)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.folder_label)

        button_layout = QHBoxLayout()

        self.select_folder_btn = QPushButton("Select Music Folder")
        self.select_folder_btn.clicked.connect(self.select_folder)
        button_layout.addWidget(self.select_folder_btn)

        self.convert_album_btn = QPushButton("Convert Album to 16-bit")
        self.convert_album_btn.clicked.connect(self.convert_current_album)
        self.convert_album_btn.setEnabled(False)
        button_layout.addWidget(self.convert_album_btn)

        self.convert_all_btn = QPushButton("Convert All")
        self.convert_all_btn.clicked.connect(self.convert_all_albums)
        self.convert_all_btn.setEnabled(False)
        button_layout.addWidget(self.convert_all_btn)

        main_layout.addLayout(button_layout)

        panels_layout = QHBoxLayout()

        self.album_list = QListWidget()
        self.album_list.itemSelectionChanged.connect(self.album_selected)
        panels_layout.addWidget(self.album_list, 1)

        self.file_list = QListWidget()
        panels_layout.addWidget(self.file_list, 2)

        main_layout.addLayout(panels_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        main_layout.addWidget(self.output_text)

        if shutil.which("ffmpeg") is None:
            self.convert_album_btn.setEnabled(False)
            self.convert_all_btn.setEnabled(False)
            self.output_text.append(
                "ffmpeg was not found on PATH. Install it to enable conversion."
            )

    def select_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if folder_path:
            self.folder_label.setText(f"Folder: {folder_path}")
            self.load_albums(folder_path)

    def load_albums(self, folder_path):
        self.album_list.clear()
        self.file_list.clear()
        self.current_album = None
        self.convert_album_btn.setEnabled(False)
        self.progress_bar.setVisible(False)

        self.albums = find_albums(folder_path)
        if not self.albums:
            self.file_list.addItem("No album folders with .flac or .mp3 found")
            self.convert_all_btn.setEnabled(False)
            return

        for album in self.albums:
            item = QListWidgetItem(album.name)
            item.setData(Qt.ItemDataRole.UserRole, str(album))
            self.album_list.addItem(item)

        self.convert_all_btn.setEnabled(True)
        self.output_text.append(f"Found {len(self.albums)} album(s).")

    def album_selected(self):
        selected = self.album_list.selectedItems()
        if not selected:
            return

        self.current_album = Path(selected[0].data(Qt.ItemDataRole.UserRole))
        self.convert_album_btn.setEnabled(self.worker is None)
        self.populate_files(self.current_album)

    def populate_files(self, album_path):
        self.file_list.clear()
        for file in list_audio_files(album_path):
            self.file_list.addItem(format_file_label(file))

    def _collect_files(self, albums):
        files = []
        for album in albums:
            files.extend(list_audio_files(album))
        return files

    def convert_current_album(self):
        if self.current_album is None:
            return
        self.start_conversion(self._collect_files([self.current_album]))

    def convert_all_albums(self):
        if not self.albums:
            return
        self.start_conversion(self._collect_files(self.albums))

    def start_conversion(self, file_paths):
        if self.worker is not None or not file_paths:
            return

        self.select_folder_btn.setEnabled(False)
        self.convert_album_btn.setEnabled(False)
        self.convert_all_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.output_text.append(f"Converting {len(file_paths)} file(s)...")

        self.worker = DownsampleWorker(file_paths)
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.finished_signal.connect(self.conversion_finished)
        self.worker.start()

    def update_progress(self, current, total):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)

    def append_log(self, message):
        self.output_text.append(message)

    def conversion_finished(self):
        self.worker = None
        self.select_folder_btn.setEnabled(True)
        self.convert_all_btn.setEnabled(bool(self.albums))
        if self.current_album is not None:
            self.convert_album_btn.setEnabled(True)
            self.populate_files(self.current_album)


def main():
    app = QApplication(sys.argv)
    window = DownsampleGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
