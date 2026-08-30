import sys
import io
import shutil
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
    QProgressBar,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QPainter
from PIL import Image
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


def extract_art(file_path):
    try:
        if file_path.suffix.lower() == ".flac":
            audio = FLAC(file_path)
            if audio.pictures:
                return Image.open(io.BytesIO(audio.pictures[0].data))
        elif file_path.suffix.lower() == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                return None
            for key in tags.keys():
                if key.startswith("APIC:"):
                    return Image.open(io.BytesIO(tags[key].data))
    except Exception:
        pass
    return None


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


class ApplyWorker(QThread):
    progress_signal = Signal(int, int)
    updated_signal = Signal(int, object, object)
    error_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, file_paths, album_title, cache):
        super().__init__()
        self.file_paths = file_paths
        self.album_title = album_title
        self._cache = cache

    def run(self):
        total = len(self.file_paths)
        for index, file_path in enumerate(self.file_paths):
            new_track_number = str(index + 1)
            try:
                if file_path.suffix.lower() == ".flac":
                    self.update_flac_metadata(
                        file_path,
                        new_track_number,
                        self.album_title if self.album_title else None,
                    )
                elif file_path.suffix.lower() == ".mp3":
                    self.update_mp3_metadata(
                        file_path,
                        new_track_number,
                        self.album_title if self.album_title else None,
                    )

                new_path = self.rename_file(file_path, new_track_number)
                new_title = read_track_metadata(new_path, self._cache)["title"]
                self.updated_signal.emit(index, new_title, str(new_path))
            except Exception as e:
                self.error_signal.emit(f"Error processing {file_path.name}: {e}")
            self.progress_signal.emit(index + 1, total)
        self.finished_signal.emit()

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
                    title = (
                        str(audio["TIT2"][0]) if "TIT2" in audio else file_path.stem
                    )
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


class ArtListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._art = QPixmap()

    def set_art(self, pixmap):
        self._art = QPixmap(pixmap) if not pixmap.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        if not self._art.isNull():
            painter = QPainter(self.viewport())
            painter.setOpacity(0.25)
            scaled = self._art.scaled(
                self.viewport().size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(
                (self.viewport().width() - scaled.width()) // 2,
                (self.viewport().height() - scaled.height()) // 2,
                scaled,
            )
            painter.end()
        super().paintEvent(event)


class AddToPlaylistDialog(QDialog):
    def __init__(self, root_path, current_album, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Songs to Playlist")
        self.root_path = Path(root_path)
        self.current_album = Path(current_album)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Select a playlist (folder), or type a new name:")
        )

        self.playlist_list = QListWidget()
        self.playlist_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._populate_playlists()
        self.playlist_list.itemSelectionChanged.connect(self._on_list_selection)
        layout.addWidget(self.playlist_list, 1)

        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("New playlist (folder) name")
        self.new_name.textChanged.connect(self._on_name_changed)
        layout.addWidget(self.new_name)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._target = None

    def _populate_playlists(self):
        audio_extensions = {".flac", ".mp3"}
        for subdir in sorted(self.root_path.iterdir(), key=lambda p: p.name.lower()):
            if not subdir.is_dir():
                continue
            if subdir == self.current_album:
                continue
            has_audio = any(
                f.is_file() and f.suffix.lower() in audio_extensions
                for f in subdir.iterdir()
            )
            item = QListWidgetItem(subdir.name)
            item.setData(Qt.ItemDataRole.UserRole, str(subdir))
            item.setFlags(
                item.flags() & ~Qt.ItemFlag.ItemIsEnabled
                if not has_audio
                else item.flags()
            )
            self.playlist_list.addItem(item)

    def _on_list_selection(self):
        selected = self.playlist_list.selectedItems()
        if selected:
            self._target = Path(selected[0].data(Qt.ItemDataRole.UserRole))
            self.new_name.clear()

    def _on_name_changed(self, text):
        if text.strip():
            self._target = self.root_path / text.strip()
        else:
            self._target = None

    def target(self):
        return self._target


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

        left_panel = QVBoxLayout()
        self.album_list = QListWidget()
        self.album_list.itemSelectionChanged.connect(self.album_selected)
        left_panel.addWidget(self.album_list, 1)
        panels_layout.addLayout(left_panel, 1)

        self.file_list = ArtListWidget()
        self.file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.file_list.viewport().setAutoFillBackground(False)
        panels_layout.addWidget(self.file_list, 2)

        main_layout.addLayout(panels_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        bottom_layout = QHBoxLayout()

        album_label = QLabel("Album Title:")
        bottom_layout.addWidget(album_label)

        self.album_input = QLineEdit()
        self.album_input.setPlaceholderText("Enter album title")
        bottom_layout.addWidget(self.album_input, 1)

        self.add_btn = QPushButton("Add to Playlist...")
        self.add_btn.clicked.connect(self.add_to_playlist)
        self.add_btn.setEnabled(False)
        bottom_layout.addWidget(self.add_btn)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply_changes)
        self.apply_btn.setEnabled(False)
        bottom_layout.addWidget(self.apply_btn)

        main_layout.addLayout(bottom_layout)

        self.current_album = None
        self._meta_cache = {}
        self.track_worker = None
        self.apply_worker = None

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
        self.add_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self._clear_art()

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
        self.add_btn.setEnabled(True)
        self.load_album_art(album_path)
        self.start_track_load(album_path)

    def load_album_art(self, album_path):
        self._clear_art()

        folder = Path(album_path)
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in {".flac", ".mp3"}:
                img = extract_art(file)
                if img:
                    self.display_art(img)
                    return

    def display_art(self, img):
        try:
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.getvalue())
            self.file_list.set_art(pixmap)
        except Exception:
            self._clear_art()

    def _clear_art(self):
        self.file_list.set_art(QPixmap())

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
        if self.apply_worker is worker:
            self.apply_worker = None
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

    def add_to_playlist(self):
        if not self.current_album or self.apply_worker is not None:
            return

        selected = self.file_list.selectedItems()
        if not selected:
            QMessageBox.information(
                self, "Add to Playlist", "Select one or more songs to add."
            )
            return

        root = self.current_album.parent
        dialog = AddToPlaylistDialog(root, self.current_album, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        target = dialog.target()
        if target is None:
            return
        if target == self.current_album:
            QMessageBox.information(
                self, "Add to Playlist", "Select a different playlist (folder)."
            )
            return

        source_paths = [
            Path(item.data(Qt.ItemDataRole.UserRole))
            for item in selected
            if item.data(Qt.ItemDataRole.UserRole)
        ]

        target.mkdir(parents=True, exist_ok=True)
        added = 0
        skipped = 0
        for source in source_paths:
            dest = target / source.name
            if dest.exists():
                skipped += 1
                continue
            try:
                shutil.copy2(source, dest)
                added += 1
            except Exception as e:
                QMessageBox.critical(
                    self, "Add to Playlist", f"Failed to copy {source.name}: {e}"
                )

        if added or skipped:
            msg = f"Added {added} song(s) to '{target.name}'."
            if skipped:
                msg += f" {skipped} song(s) already existed and were skipped."
            QMessageBox.information(self, "Add to Playlist", msg)

    def apply_changes(self):
        if not self.current_album or self.apply_worker is not None:
            return

        album_title = self.album_input.text().strip()

        file_paths = []
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            file_path_data = item.data(Qt.ItemDataRole.UserRole)
            if file_path_data:
                file_paths.append(Path(file_path_data))

        if not file_paths:
            return

        self.apply_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        worker = ApplyWorker(file_paths, album_title, self._meta_cache)
        worker.progress_signal.connect(self.update_progress)
        worker.updated_signal.connect(self.on_track_updated)
        worker.error_signal.connect(self.on_apply_error)
        worker.finished_signal.connect(self.on_apply_finished)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self.apply_worker = worker
        worker.start()

    def update_progress(self, current, total):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)

    def on_track_updated(self, index, new_title, new_path):
        item = self.file_list.item(index)
        if item:
            item.setText(new_title)
            item.setData(Qt.ItemDataRole.UserRole, new_path)

    def on_apply_error(self, message):
        print(message)

    def on_apply_finished(self):
        self.apply_btn.setEnabled(True)
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.start_track_load(self.current_album)


def main():
    app = QApplication(sys.argv)
    window = AudioFileReader()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
