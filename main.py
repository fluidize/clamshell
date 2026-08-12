import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
from cover_converter import CoverConverterGUI
from playlist_organizer import AudioFileReader


class ClamshellGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Clamshell")
        self.setGeometry(100, 100, 600, 500)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.tabs.addTab(AudioFileReader(), "Playlist Organizer")
        self.tabs.addTab(CoverConverterGUI(), "Cover Converter")
        


def main():
    app = QApplication(sys.argv)
    window = ClamshellGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
