import sys
import os
import io
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QLabel,
    QTextEdit,
)
from PySide6.QtCore import Qt, QThread, Signal
from PIL import Image
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.flac import FLAC, Picture


class ConversionWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, folder_path):
        super().__init__()
        self.folder_path = folder_path
        self.should_stop = False

    def run(self):
        for root, dirs, files in os.walk(self.folder_path):
            if self.should_stop:
                break
            for file in files:
                if self.should_stop:
                    break
                full_path = os.path.join(root, file)
                if file.lower().endswith(".mp3"):
                    self.convert_png_to_jpeg_mp3(full_path)
                elif file.lower().endswith(".flac"):
                    self.convert_png_to_jpeg_flac(full_path)
        self.finished_signal.emit()

    def convert_png_to_jpeg_mp3(self, file_path):
        try:
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                return

            modified = False
            for key in list(tags.keys()):
                if key.startswith("APIC:"):
                    apic = tags[key]

                    if apic.mime == "image/png" or b"PNG" in apic.data[:10]:
                        self.log_signal.emit(
                            f"[MP3] Found PNG artwork in: {os.path.basename(file_path)}"
                        )

                        img = Image.open(io.BytesIO(apic.data))
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")

                        output_buffer = io.BytesIO()
                        img.save(output_buffer, format="JPEG", quality=90)
                        jpeg_data = output_buffer.getvalue()

                        apic.mime = "image/jpeg"
                        apic.data = jpeg_data
                        modified = True

            if modified:
                tags.save()
                self.log_signal.emit(
                    f"[MP3] Successfully converted artwork to JPEG: {os.path.basename(file_path)}"
                )
        except Exception as e:
            self.log_signal.emit(
                f"Error processing MP3 {os.path.basename(file_path)}: {e}"
            )

    def convert_png_to_jpeg_flac(self, file_path):
        try:
            audio = FLAC(file_path)
            if not audio.pictures:
                return

            modified = False
            new_pictures = []

            for pic in audio.pictures:
                if pic.mime == "image/png" or b"PNG" in pic.data[:10]:
                    self.log_signal.emit(
                        f"[FLAC] Found PNG artwork in: {os.path.basename(file_path)}"
                    )

                    img = Image.open(io.BytesIO(pic.data))
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")

                    output_buffer = io.BytesIO()
                    img.save(output_buffer, format="JPEG", quality=90)
                    jpeg_data = output_buffer.getvalue()

                    new_pic = Picture()
                    new_pic.type = pic.type
                    new_pic.desc = pic.desc
                    new_pic.mime = "image/jpeg"
                    new_pic.width = img.width
                    new_pic.height = img.height
                    new_pic.depth = 24  # default jpeg color depth
                    new_pic.data = jpeg_data

                    new_pictures.append(new_pic)
                    modified = True
                else:
                    new_pictures.append(pic)

            if modified:
                audio.clear_pictures()
                for np in new_pictures:
                    audio.add_picture(np)
                audio.save()
                self.log_signal.emit(
                    f"[FLAC] Successfully converted artwork to JPEG: {os.path.basename(file_path)}"
                )
        except Exception as e:
            self.log_signal.emit(
                f"Error processing FLAC {os.path.basename(file_path)}: {e}"
            )

    def stop(self):
        self.should_stop = True


class CoverConverterGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cover Art Converter")

        main_layout = QVBoxLayout(self)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.folder_label)

        button_layout = QHBoxLayout()

        self.select_folder_btn = QPushButton("Select Folder")
        self.select_folder_btn.clicked.connect(self.select_folder)
        button_layout.addWidget(self.select_folder_btn)

        self.convert_btn = QPushButton("Convert Covers")
        self.convert_btn.clicked.connect(self.start_conversion)
        self.convert_btn.setEnabled(False)
        button_layout.addWidget(self.convert_btn)

        main_layout.addLayout(button_layout)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        main_layout.addWidget(self.output_text)

        self.current_folder = None
        self.worker = None

    def select_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if folder_path:
            self.current_folder = folder_path
            self.folder_label.setText(f"Folder: {folder_path}")
            self.convert_btn.setEnabled(True)
            self.output_text.append(f"Selected folder: {folder_path}")

    def start_conversion(self):
        if not self.current_folder:
            return

        self.convert_btn.setEnabled(False)
        self.select_folder_btn.setEnabled(False)
        self.output_text.append("Starting conversion...")

        self.worker = ConversionWorker(self.current_folder)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.conversion_finished)
        self.worker.start()

    def append_log(self, message):
        self.output_text.append(message)

    def conversion_finished(self):
        self.convert_btn.setEnabled(True)
        self.select_folder_btn.setEnabled(True)
        self.output_text.append("Conversion completed!")
        self.worker = None


def main():
    app = QApplication(sys.argv)
    window = CoverConverterGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
