"""Reusable GTK/libadwaita widgets shared by the three pages."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk, Pango
from PIL import Image, ImageFilter

from ..core.audio import list_audio_files


def dispatch(callback: Callable, *args) -> None:
    """Run *callback* on the GTK main thread."""
    GLib.idle_add(callback, *args)


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


def pil_to_png_bytes(image: Image.Image, max_size: int = 900) -> bytes | None:
    """Prepare album artwork as a soft, blurred background texture."""
    try:
        image = image.convert("RGB")
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        radius = max(4, round(image.width / 50))
        image = image.filter(ImageFilter.GaussianBlur(radius))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


def texture_from_png_bytes(data: bytes | None) -> Gdk.Texture | None:
    if not data:
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except Exception:
        return None


def choose_folder(
    parent: Gtk.Widget,
    on_selected: Callable[[Path], None],
    title: str = "Select Music Folder",
) -> None:
    dialog = Gtk.FileDialog(title=title)
    dialog.set_modal(True)

    def finished(file_dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = file_dialog.select_folder_finish(result)
        except GLib.Error:
            return
        except Exception:
            return
        if folder is not None and folder.get_path():
            on_selected(Path(folder.get_path()))

    dialog.select_folder(parent, None, finished)


def show_alert(
    parent: Gtk.Widget | None,
    heading: str,
    body: str = "",
) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("close", "Close")
    dialog.set_default_response("close")
    dialog.set_close_response("close")
    if parent is not None:
        dialog.present(parent)
    else:
        dialog.present(None)


class FolderChooserRow(Adw.ActionRow):
    def __init__(
        self,
        on_choose: Callable[[], None],
        title: str = "Music Folder",
    ) -> None:
        super().__init__(title=title, subtitle="No folder selected")
        self._on_choose = on_choose
        self.set_subtitle_lines(2)
        self.set_activatable(False)

        button = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text("Select a folder")
        button.connect("clicked", lambda _button: self._on_choose())
        self.add_suffix(button)

    def set_folder(self, folder: Path | None) -> None:
        if folder is None:
            self.set_subtitle("No folder selected")
            self.set_tooltip_text(None)
        else:
            self.set_subtitle(str(folder))
            self.set_tooltip_text(str(folder))


class LogView(Gtk.Box):
    """A card with a scrolling, read-only monospace log."""

    MAX_LINES = 1500
    TRIM_LINES = 500

    def __init__(self, title: str = "Log", min_height: int = 140) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=12,
            margin_end=6,
            margin_top=6,
        )
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.add_css_class("heading")
        clear_button = Gtk.Button(
            icon_name="edit-clear-all-symbolic",
            tooltip_text="Clear Log",
            valign=Gtk.Align.CENTER,
        )
        clear_button.add_css_class("flat")
        clear_button.connect("clicked", lambda _button: self.clear())
        header.append(label)
        header.append(clear_button)
        self.append(header)

        self.textview = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        self.textview.set_top_margin(8)
        self.textview.set_bottom_margin(8)
        self.textview.set_left_margin(12)
        self.textview.set_right_margin(12)
        self.textview.add_css_class("log-text")

        scroller = Gtk.ScrolledWindow(
            vexpand=True,
            hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroller.set_min_content_height(min_height)
        scroller.set_child(self.textview)
        scroller.add_css_class("log-scroller")
        self.append(scroller)

        self._buffer = self.textview.get_buffer()

    def add_line(self, message: str) -> None:
        end = self._buffer.get_end_iter()
        if self._buffer.get_char_count():
            self._buffer.insert(end, "\n" + message)
        else:
            self._buffer.insert(end, message)

        if self._buffer.get_line_count() > self.MAX_LINES:
            start = self._buffer.get_start_iter()
            cut = self._buffer.get_iter_at_line(self.TRIM_LINES)
            self._buffer.delete(start, cut)

        self.textview.scroll_to_iter(
            self._buffer.get_end_iter(), 0.0, False, 0.0, 0.0
        )

    def clear(self) -> None:
        self._buffer.set_text("")


class TaskBar(Gtk.Box):
    """Progress bar plus cancel button, hidden while idle."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("task-bar")
        self.set_valign(Gtk.Align.CENTER)

        self.label = Gtk.Label(label="", xalign=0, width_chars=14)
        self.label.add_css_class("caption")
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_max_width_chars(24)
        self.progress = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        self.progress.set_show_text(False)
        self.cancel_button = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self.cancel_button.add_css_class("flat")
        self.cancel_button.connect("clicked", self._on_cancel)

        self.append(self.label)
        self.append(self.progress)
        self.append(self.cancel_button)

        self._token: CancelToken | None = None
        self.set_visible(False)

    def start(self, text: str = "Working…", cancellable: bool = True) -> CancelToken:
        token = CancelToken()
        self._token = token
        self.label.set_text(text)
        self.progress.set_fraction(0.0)
        self.cancel_button.set_visible(cancellable)
        self.cancel_button.set_sensitive(True)
        self.set_visible(True)
        return token

    def update(self, current: int, total: int) -> None:
        if total > 0:
            current = min(current, total)
            self.progress.set_fraction(current / total)
            self.label.set_text(f"{current} / {total}")

    def set_text(self, text: str) -> None:
        self.label.set_text(text)

    def finish(self) -> None:
        self._token = None
        self.set_visible(False)
        self.progress.set_fraction(0.0)

    def _on_cancel(self, button: Gtk.Button) -> None:
        if self._token is not None:
            self._token.cancel()
            self.set_text("Cancelling…")
            button.set_sensitive(False)


class AlbumList(Gtk.Box):
    """Sidebar listing album folders under the selected music root."""

    def __init__(
        self,
        on_open_folder: Callable[[], None],
        on_selected: Callable[[Path], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("sidebar-panel")
        self._on_selected = on_selected
        self._rows: dict[str, Adw.ActionRow] = {}

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=12,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
        )
        title = Gtk.Label(label="Albums", xalign=0, hexpand=True)
        title.add_css_class("sidebar-title")
        self.open_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text="Open Music Folder (Ctrl+O)",
            valign=Gtk.Align.CENTER,
        )
        self.open_button.add_css_class("flat")
        self.open_button.connect("clicked", lambda _button: on_open_folder())
        header.append(title)
        header.append(self.open_button)
        self.append(header)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self._on_row_selected)

        scroller = Gtk.ScrolledWindow(
            vexpand=True,
            hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroller.set_child(self.listbox)
        self.append(scroller)

    def set_albums(self, albums: list[Path]) -> None:
        self.listbox.unselect_all()
        self._clear_rows()
        self._rows.clear()

        if not albums:
            placeholder = Adw.ActionRow(title="No albums found")
            placeholder.set_activatable(False)
            placeholder.set_selectable(False)
            placeholder.add_css_class("dim-label")
            self.listbox.append(placeholder)
            return

        for album in albums:
            try:
                count = len(list_audio_files(album))
            except OSError:
                count = 0
            subtitle = f"{count} track" if count == 1 else f"{count} tracks"
            row = Adw.ActionRow(title=album.name, subtitle=subtitle)
            row.album_path = album
            self._rows[str(album)] = row
            self.listbox.append(row)

        first = self.listbox.get_row_at_index(0)
        if first is not None:
            self.listbox.select_row(first)

    def clear(self) -> None:
        self.listbox.unselect_all()
        self._clear_rows()
        self._rows.clear()

    def select(self, album: Path) -> None:
        row = self._rows.get(str(album))
        if row is not None:
            self.listbox.select_row(row)

    def _clear_rows(self) -> None:
        while True:
            row = self.listbox.get_row_at_index(0)
            if row is None:
                break
            self.listbox.remove(row)

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        album = getattr(row, "album_path", None)
        if album is not None:
            self._on_selected(album)
