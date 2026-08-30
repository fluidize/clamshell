import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
from theme import apply_theme
from cover_converter import CoverConverterGUI
from playlist_organizer import AudioFileReader


class ClamshellGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Clamshell")
        self.setGeometry(100, 100, 600, 500)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        
        self.tabs.addTab(AudioFileReader(), "Playlist Organizer")
        self.tabs.addTab(CoverConverterGUI(), "Cover Converter")
        self.tabs.tabBar().setExpanding(False)



def main():
    app = QApplication(sys.argv)
    apply_theme(app)
    window = ClamshellGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
