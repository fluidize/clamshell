import sys
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
)
from PySide6.QtCore import Qt, QThread, Signal
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.id3._frames import TIT2, TALB, TRCK


def read_track_metadata(file_path, cache):
    try:
        mtime = file_path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = str(file_path)
    cached = cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    meta = {"track": "", "title": file_path.stem, "album": ""}
    try:
        if file_path.suffix.lower() == ".flac":
            audio = FLAC(file_path)
            track_number = audio.get("tracknumber", [""])[0]
            title = audio.get("title", [file_path.stem])[0]
            album = audio.get("album", [""])[0]
            meta = {"track": track_number, "title": title, "album": album}
        elif file_path.suffix.lower() == ".mp3":
            audio = MP3(file_path)
            track_number = str(audio["TRCK"][0]) if "TRCK" in audio else ""
            title = str(audio["TIT2"][0]) if "TIT2" in audio else file_path.stem
            album = str(audio["TALB"][0]) if "TALB" in audio else ""
            meta = {"track": track_number, "title": title, "album": album}
    except Exception as e:
        print(f"Error reading metadata for {file_path.name}: {e}")

    if "/" in meta["track"]:
        meta["track"] = meta["track"].split("/")[0]
    if not meta["title"]:
        meta["title"] = file_path.stem

    cache[key] = (mtime, meta)
    return meta


class TrackLoadWorker(QThread):
    loaded_signal = Signal(str, object)

    def __init__(self, album_path, cache):
        super().__init__()
        self.album_path = album_path
        self._cache = cache

    def run(self):
        folder = Path(self.album_path)
        audio_extensions = {".flac", ".mp3"}

        entries = []
        for file in folder.iterdir():
            if file.is_file() and file.suffix.lower() in audio_extensions:
                entries.append((file, read_track_metadata(file, self._cache)))

        def sort_key(entry):
            track_num = entry[1]["track"]
            if track_num:
                try:
                    return (0, int(track_num))
                except ValueError:
                    return (1, track_num)
            return (2, entry[0].name)

        entries.sort(key=sort_key)
        self.loaded_signal.emit(self.album_path, entries)


class AudioFileReader(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Playlist Organizer")

        main_layout = QVBoxLayout(self)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.folder_label)

        self.select_folder_btn = QPushButton("Select Music Folder")
        self.select_folder_btn.clicked.connect(self.select_folder)
        main_layout.addWidget(self.select_folder_btn)

        panels_layout = QHBoxLayout()

        self.album_list = QListWidget()
        self.album_list.itemSelectionChanged.connect(self.album_selected)
        panels_layout.addWidget(self.album_list, 1)

        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        panels_layout.addWidget(self.file_list, 2)

        main_layout.addLayout(panels_layout)

        bottom_layout = QHBoxLayout()

        album_label = QLabel("Album Title:")
        bottom_layout.addWidget(album_label)

        self.album_input = QLineEdit()
        self.album_input.setPlaceholderText("Enter album title")
        bottom_layout.addWidget(self.album_input, 1)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply_changes)
        self.apply_btn.setEnabled(False)
        bottom_layout.addWidget(self.apply_btn)

        main_layout.addLayout(bottom_layout)

        self.current_album = None
        self._meta_cache = {}
        self.track_worker = None

    def select_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if folder_path:
            self.folder_label.setText(f"Folder: {folder_path}")
            self.load_albums(folder_path)

    def load_albums(self, folder_path):
        self.album_list.clear()
        self.file_list.clear()
        self.album_input.clear()
        self.current_album = None
        self._meta_cache.clear()
        self.apply_btn.setEnabled(False)

        root = Path(folder_path)
        audio_extensions = {".flac", ".mp3"}

        albums = []
        for subdir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not subdir.is_dir():
                continue
            has_audio = any(
                f.is_file() and f.suffix.lower() in audio_extensions
                for f in subdir.iterdir()
            )
            if has_audio:
                albums.append(subdir)

        if not albums:
            self.file_list.addItem("No album subfolders with .flac or .mp3 found")
            return

        for album in albums:
            item = QListWidgetItem(album.name)
            item.setData(Qt.ItemDataRole.UserRole, str(album))
            self.album_list.addItem(item)

    def album_selected(self):
        selected = self.album_list.selectedItems()
        if not selected:
            return

        album_path = Path(selected[0].data(Qt.ItemDataRole.UserRole))
        self.current_album = album_path
        self.apply_btn.setEnabled(True)
        self.start_track_load(album_path)

    def start_track_load(self, album_path):
        self.file_list.clear()
        self.album_input.clear()

        loading = QListWidgetItem("Loading tracks...")
        loading.setFlags(Qt.ItemFlag.NoItemFlags)
        self.file_list.addItem(loading)

        worker = TrackLoadWorker(str(album_path), self._meta_cache)
        worker.loaded_signal.connect(self.on_tracks_loaded)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self.track_worker = worker
        worker.start()

    def _cleanup_worker(self, worker):
        if self.track_worker is worker:
            self.track_worker = None
        worker.deleteLater()

    def on_tracks_loaded(self, album_path, entries):
        if album_path != str(self.current_album):
            return

        self.file_list.clear()
        if entries:
            for file, meta in entries:
                item = QListWidgetItem(meta["title"])
                item.setData(
                    Qt.ItemDataRole.UserRole, str(file)
                )  # Store file path as user data
                self.file_list.addItem(item)
        else:
            self.file_list.addItem("No .flac or .mp3 files found in this folder")

        self.album_input.setText(self.get_album_title(entries, Path(album_path)))

    def get_album_title(self, entries, album_path):
        for _, meta in entries:
            if meta["album"]:
                return meta["album"]
        return album_path.name

    def apply_changes(self):
        if not self.current_album:
            return

        album_title = self.album_input.text().strip()

        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            file_path_data = item.data(Qt.ItemDataRole.UserRole)
            if not file_path_data:
                continue
            file_path = Path(file_path_data)
            new_track_number = str(index + 1)

            try:
                if file_path.suffix.lower() == ".flac":
                    self.update_flac_metadata(
                        file_path,
                        new_track_number,
                        album_title if album_title else None,
                    )
                elif file_path.suffix.lower() == ".mp3":
                    self.update_mp3_metadata(
                        file_path,
                        new_track_number,
                        album_title if album_title else None,
                    )

                new_path = self.rename_file(file_path, new_track_number)

                new_title = read_track_metadata(new_path, self._meta_cache)["title"]
                item.setText(new_title)
                item.setData(Qt.ItemDataRole.UserRole, str(new_path))

            except Exception as e:
                print(f"Error processing {file_path.name}: {e}")

        self.start_track_load(self.current_album)

    def update_flac_metadata(self, file_path, track_number, album_title):
        audio = FLAC(file_path)
        audio["tracknumber"] = track_number
        if album_title:
            audio["album"] = album_title
        audio.save()

    def update_mp3_metadata(self, file_path, track_number, album_title):
        try:
            audio = MP3(file_path)
        except ID3NoHeaderError:
            audio = MP3(file_path)
            audio.add_tags()

        audio["TRCK"] = TRCK(encoding=3, text=track_number)

        if album_title:
            audio["TALB"] = TALB(encoding=3, text=album_title)

        audio.save()

    def rename_file(self, file_path, track_number):
        try:
            if file_path.suffix.lower() == ".flac":
                audio = FLAC(file_path)
                title = audio.get("title", [file_path.stem])[0]
            elif file_path.suffix.lower() == ".mp3":
                audio = MP3(file_path)
                try:
                    title = str(audio["TIT2"][0]) if "TIT2" in audio else file_path.stem
                except (KeyError, IndexError):
                    title = file_path.stem
            else:
                title = file_path.stem
        except Exception:
            title = file_path.stem

        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            title = title.replace(char, "")

        new_name = f"{track_number} - {title}{file_path.suffix}"
        new_path = file_path.parent / new_name

        if new_path != file_path:
            file_path.rename(new_path)
            return new_path
        return file_path


def main():
    app = QApplication(sys.argv)
    window = AudioFileReader()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
