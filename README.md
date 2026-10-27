# Clamshell

A collection of tools for .flac and .mp3 music handling.

Two front ends are included:

- `clamshell_gtk/` — native GTK 4 / libadwaita
- `clamshell_qt/` — the original PySide6 app

## GTK 4 / libadwaita edition (new)

`clamshell_gtk/` is a native GNOME implementation with adaptive layouts,
light/dark support, toasts, header bars, cancellable background jobs, and
blurred album-art backdrops. It uses the stock GNOME `audio-x-generic` icon,
so the installer only adds the launcher and desktop entry.

- **Playlist Organizer** – browse album folders, reorder tracks by dragging,
  rewrite track numbers and album titles, rename files to `NN - Title.ext`,
  and copy songs between playlist folders. Renames never overwrite a file.
- **Cover Converter** – replace embedded PNG artwork with JPEG in FLAC and
  MP3 files, recursively, with selectable quality. Transparency is flattened
  onto white before encoding.
- **Downsampler** – convert 24-bit+ FLAC albums to 16-bit with dithering,
  preserving Vorbis comments, cuesheets, pictures and file metadata. Every
  result is validated (bit depth, channel count, sample rate, sample count,
  and a full decode test) before replacing the original. The `flac` reference
  decoder is used automatically if ffmpeg fails.

### Requirements

- GTK 4.10+ and libadwaita 1.5+ (tested with GTK 4.24 and libadwaita 1.10)
- PyGObject, plus `mutagen` and `Pillow`
- `ffmpeg` on `PATH`; `flac` is optional (enables the fallback decoder and
  faster validation)

On Debian/Ubuntu:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 ffmpeg flac
```

### Run

```sh
./run-gtk.sh
```

The script creates a `.venv-gtk` that inherits system PyGObject packages and
installs `mutagen` and `Pillow` if needed. You can also run it manually:

```sh
python3 -m venv --system-site-packages .venv-gtk
.venv-gtk/bin/pip install -r requirements-gtk.txt
.venv-gtk/bin/python -m clamshell_gtk
```

To add it to the app grid:

```sh
./install-gtk.sh
```

To remove the launcher and desktop entry again:

```sh
./uninstall-gtk.sh
```

Use `./uninstall-gtk.sh --purge` to also delete the local `.venv-gtk`.
The GTK source tree itself is never removed.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+O` | Open a music folder in the current view |
| `Ctrl+1/2/3` | Switch to Playlist Organizer / Cover Converter / Downsampler |
| `Ctrl+W` | Close the window |
| `Ctrl+Q` | Quit |

## Qt / PySide6 edition

The original single-window app lives in `clamshell_qt/` and is unchanged apart
from its location.

- `clamshell_qt/main.py`: all-in-one PySide6 app combining all three tools
- `clamshell_qt/cover_converter.py`: convert cover art from .png to .jpeg
- `clamshell_qt/playlist_organizer.py`: organize and create custom playlist folders
- `clamshell_qt/downsampler.py`: convert 24-bit+ FLAC albums down to 16-bit (requires ffmpeg on PATH)

### Run

```sh
./run-qt.sh
```

The Qt virtualenv stays at the repository root as `.venv` (virtualenvs are not
relocatable). The script creates it and installs the requirements if needed.
To run manually:

```sh
python3 -m venv .venv
.venv/bin/pip install -r clamshell_qt/requirements.txt
.venv/bin/python clamshell_qt/main.py
```

