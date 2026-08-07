import sys
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
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
from PySide6.QtCore import Qt
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.id3._frames import TIT2, TALB, TRCK


class AudioFileReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio File Reader")
        self.setGeometry(100, 100, 600, 500)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.folder_label)

        self.select_folder_btn = QPushButton("Select Folder")
        self.select_folder_btn.clicked.connect(self.select_folder)
        main_layout.addWidget(self.select_folder_btn)

        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        main_layout.addWidget(self.file_list)

        bottom_layout = QHBoxLayout()

        album_label = QLabel("Album Title:")
        bottom_layout.addWidget(album_label)

        self.album_input = QLineEdit()
        self.album_input.setPlaceholderText("Enter album title")
        bottom_layout.addWidget(self.album_input, 1)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply_changes)
        bottom_layout.addWidget(self.apply_btn)

        main_layout.addLayout(bottom_layout)

        self.current_folder = None

    def select_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder_path:
            self.current_folder = folder_path
            self.folder_label.setText(f"Folder: {folder_path}")
            self.load_audio_files(folder_path)

    def get_track_number(self, file_path):
        try:
            if file_path.suffix.lower() == ".flac":
                audio = FLAC(file_path)
                track_number = audio.get("tracknumber", [""])[0]
            elif file_path.suffix.lower() == ".mp3":
                audio = MP3(file_path)
                try:
                    track_number = str(audio["TRCK"][0]) if "TRCK" in audio else ""
                except (KeyError, IndexError):
                    track_number = ""
            else:
                return None

            if "/" in track_number:
                track_number = track_number.split("/")[0]

            return track_number if track_number else None
        except Exception as e:
            print(f"Error reading track number for {file_path.name}: {e}")
            return None

    def get_track_title(self, file_path):
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
                return file_path.stem
            return title
        except Exception as e:
            print(f"Error reading title for {file_path.name}: {e}")
            return file_path.stem

    def load_audio_files(self, folder_path):
        self.file_list.clear()

        folder = Path(folder_path)
        audio_extensions = {".flac", ".mp3"}

        audio_files = []
        for file in folder.iterdir():
            if file.is_file() and file.suffix.lower() in audio_extensions:
                audio_files.append(file)

        def sort_key(file):
            track_num = self.get_track_number(file)
            if track_num:
                try:
                    return (0, int(track_num))
                except ValueError:
                    return (1, track_num)
            return (2, file.name)

        audio_files.sort(key=sort_key)

        if audio_files:
            for file in audio_files:
                title = self.get_track_title(file)
                item = QListWidgetItem(title)
                item.setData(
                    Qt.ItemDataRole.UserRole, str(file)
                )  # Store file path as user data
                self.file_list.addItem(item)
        else:
            self.file_list.addItem("No .flac or .mp3 files found in this folder")

    def apply_changes(self):
        if not self.current_folder:
            return

        album_title = self.album_input.text().strip()

        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            file_path = Path(item.data(Qt.ItemDataRole.UserRole))
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

                new_title = self.get_track_title(new_path)
                item.setText(new_title)
                item.setData(Qt.ItemDataRole.UserRole, str(new_path))

            except Exception as e:
                print(f"Error processing {file_path.name}: {e}")

        self.load_audio_files(self.current_folder)

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
