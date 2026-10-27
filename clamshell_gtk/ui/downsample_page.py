"""Downsampler page: convert high-resolution FLAC albums to 16-bit."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from gi.repository import Adw, GObject, Gtk, Pango

from ..core.audio import (
    album_directories,
    format_file_label,
    list_audio_files,
)
from ..core.downsample import DownsampleJob
from .common import (
    AlbumList,
    CancelToken,
    LogView,
    TaskBar,
    choose_folder,
    dispatch,
    show_alert,
)


class DownsamplePage(Gtk.Box):
    def __init__(self, toast_overlay: Adw.ToastOverlay) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._toast_overlay = toast_overlay
        self._root: Path | None = None
        self._album: Path | None = None
        self._albums: list[Path] = []
        self._busy = False
        self._load_generation = 0
        self._active_token: CancelToken | None = None
        self._ffmpeg_available = shutil.which("ffmpeg") is not None
        self._build()

    def _build(self) -> None:
        if not self._ffmpeg_available:
            banner = Adw.Banner(
                title=(
                    "ffmpeg was not found on PATH. Install it to enable "
                    "conversion."
                ),
                revealed=True,
            )
            self.append(banner)

        self.split = Adw.OverlaySplitView()
        self.split.set_show_sidebar(True)
        self.split.set_min_sidebar_width(240)
        self.split.set_max_sidebar_width(360)
        self.split.set_sidebar_width_fraction(0.28)
        self.split.set_vexpand(True)

        self.album_list = AlbumList(
            on_open_folder=self.open_folder,
            on_selected=self._on_album_selected,
        )
        self.split.set_sidebar(self.album_list)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        self.stack.set_vexpand(True)
        self.stack.add_named(
            self._make_status_page(
                "folder-music-symbolic",
                "No Folder Selected",
                "Open a folder of albums to convert high-resolution FLACs.",
                "Open Music Folder",
                self.open_folder,
            ),
            "no-folder",
        )
        self.stack.add_named(
            self._make_status_page(
                "folder-music-symbolic",
                "No Albums Found",
                "This folder has no albums with .flac or .mp3 files.",
                "Choose Another Folder",
                self.open_folder,
            ),
            "empty-folder",
        )
        self.stack.add_named(
            self._make_status_page(
                "audio-x-generic-symbolic",
                "Select an Album",
                "Pick an album from the sidebar to inspect its files.",
            ),
            "no-album",
        )
        self.stack.add_named(self._build_album_page(), "album")
        self.stack.set_visible_child_name("no-folder")
        self.split.set_content(self.stack)
        self.append(self.split)

    def _make_status_page(
        self,
        icon_name: str,
        title: str,
        description: str,
        button_label: str | None = None,
        callback=None,
    ) -> Adw.StatusPage:
        page = Adw.StatusPage(
            icon_name=icon_name,
            title=title,
            description=description,
            vexpand=True,
        )
        if button_label is not None and callback is not None:
            button = Gtk.Button(label=button_label, halign=Gtk.Align.CENTER)
            button.add_css_class("pill")
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda _button: callback())
            page.set_child(button)
        return page

    def _build_album_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=10,
            margin_bottom=6,
            margin_start=12,
            margin_end=18,
        )

        self.sidebar_toggle = Gtk.ToggleButton()
        self.sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self.sidebar_toggle.set_tooltip_text("Toggle Sidebar")
        self.sidebar_toggle.set_active(True)
        self.sidebar_toggle.add_css_class("flat")
        self.sidebar_toggle.bind_property(
            "active",
            self.split,
            "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL
            | GObject.BindingFlags.SYNC_CREATE,
        )
        header.append(self.sidebar_toggle)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        self.album_title = Gtk.Label(
            label="",
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        self.album_title.add_css_class("title-4")
        self.album_count = Gtk.Label(label="", xalign=0)
        self.album_count.add_css_class("caption")
        self.album_count.add_css_class("dim-label")
        titles.append(self.album_title)
        titles.append(self.album_count)
        header.append(titles)

        self.convert_album_button = Gtk.Button(label="Convert Album")
        self.convert_album_button.add_css_class("flat")
        self.convert_album_button.set_tooltip_text(
            "Convert all high-resolution FLACs in this album to 16-bit"
        )
        self.convert_album_button.set_sensitive(False)
        self.convert_album_button.connect(
            "clicked", lambda _button: self._convert_album()
        )
        header.append(self.convert_album_button)

        self.convert_all_button = Gtk.Button(label="Convert All")
        self.convert_all_button.add_css_class("suggested-action")
        self.convert_all_button.set_tooltip_text(
            "Convert every high-resolution FLAC in every album"
        )
        self.convert_all_button.set_sensitive(False)
        self.convert_all_button.connect(
            "clicked", lambda _button: self._convert_all()
        )
        header.append(self.convert_all_button)

        page.append(header)

        self.file_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            show_separators=False,
        )
        self.file_list.add_css_class("boxed-list")
        self.file_list.set_margin_top(6)
        self.file_list.set_margin_bottom(6)
        self.file_list.set_margin_start(18)
        self.file_list.set_margin_end(18)

        file_scroller = Gtk.ScrolledWindow(
            vexpand=True,
            hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        file_scroller.set_child(self.file_list)
        page.append(file_scroller)

        self.task_bar = TaskBar()
        page.append(self.task_bar)

        self.log_view = LogView(title="Log", min_height=130)
        self.log_view.set_vexpand(False)
        self.log_view.set_margin_start(18)
        self.log_view.set_margin_end(18)
        self.log_view.set_margin_bottom(12)
        page.append(self.log_view)
        return page

    # -- data loading --------------------------------------------------

    def open_folder(self) -> None:
        root = self.get_root()
        if root is None:
            return
        choose_folder(root, self._set_root)

    def _set_root(self, folder: Path) -> None:
        if self._busy:
            return
        self._root = folder
        self.album_list.open_button.set_tooltip_text(f"Music folder: {folder}")
        self._album = None
        self._load_generation += 1
        self._clear_files()
        self._albums = album_directories(folder, include_root=True)

        if not self._albums:
            self.album_list.clear()
            self.stack.set_visible_child_name("empty-folder")
            self._update_controls()
            return

        self.stack.set_visible_child_name("no-album")
        self.album_list.set_albums(self._albums)

    def _on_album_selected(self, album: Path) -> None:
        if self._busy:
            return
        self._album = album
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self._load_generation += 1
        generation = self._load_generation

        self.album_title.set_text(album.name)
        self.album_count.set_text("Loading…")
        self._clear_files()
        self._add_placeholder("Loading files…")
        self.stack.set_visible_child_name("album")
        self._update_controls(loaded=False)

        thread = threading.Thread(
            target=self._load_files_worker,
            args=(album, generation),
            daemon=True,
        )
        thread.start()

    def _load_files_worker(self, album: Path, generation: int) -> None:
        try:
            entries = [
                (file_path, format_file_label(file_path))
                for file_path in list_audio_files(album)
            ]
            dispatch(self._on_files_loaded, album, entries, generation)
        except Exception as exc:
            dispatch(self._on_load_error, album, str(exc), generation)

    def _on_files_loaded(
        self, album: Path, entries: list, generation: int
    ) -> None:
        if album != self._album or generation != self._load_generation:
            return

        self._clear_files()
        if not entries:
            self._add_placeholder("No .flac or .mp3 files found in this folder")
            self.album_count.set_text("No files")
            self._update_controls(loaded=False)
            return

        for file_path, (name, detail) in entries:
            row = Adw.ActionRow(title=name, subtitle=detail)
            row.set_activatable(False)
            row.set_selectable(False)
            self.file_list.append(row)

        count = len(entries)
        self.album_count.set_text(
            "1 file" if count == 1 else f"{count} files"
        )
        self._update_controls(loaded=True)

    def _on_load_error(
        self, album: Path, message: str, generation: int
    ) -> None:
        if album != self._album or generation != self._load_generation:
            return
        self._clear_files()
        self._add_placeholder("Could not read this album")
        self.album_count.set_text("—")
        self._update_controls(loaded=False)
        self._toast(f"Could not read album: {message}")

    def _clear_files(self) -> None:
        self.file_list.remove_all()

    def _add_placeholder(self, text: str) -> None:
        row = Adw.ActionRow(title=text)
        row.set_activatable(False)
        row.set_selectable(False)
        row.add_css_class("dim-label")
        self.file_list.append(row)

    def _update_controls(self, loaded: bool = True) -> None:
        has_files = self.file_list.get_row_at_index(0) is not None and loaded
        enabled = (
            self._ffmpeg_available and not self._busy and has_files
        )
        self.convert_album_button.set_sensitive(enabled)
        self.convert_all_button.set_sensitive(
            self._ffmpeg_available
            and not self._busy
            and bool(self._albums)
        )
        self.file_list.set_sensitive(not self._busy)
        self.album_list.set_sensitive(not self._busy)
        self.sidebar_toggle.set_sensitive(not self._busy)

    # -- conversion ----------------------------------------------------

    def _convert_album(self) -> None:
        if self._album is None or self._busy:
            return
        paths = list_audio_files(self._album)
        if not paths:
            self._toast("This album has no audio files.")
            return
        self._start_conversion(paths)

    def _convert_all(self) -> None:
        if not self._albums or self._busy:
            return
        paths: list[Path] = []
        for album in self._albums:
            paths.extend(list_audio_files(album))
        if not paths:
            self._toast("No audio files found.")
            return
        self._start_conversion(paths)

    def _start_conversion(self, paths: list[Path]) -> None:
        if self._busy or not self._ffmpeg_available:
            return

        self._busy = True
        self._update_controls()
        self.log_view.add_line(
            f"Converting {len(paths)} file"
            + ("" if len(paths) == 1 else "s")
            + "…"
        )
        token = self.task_bar.start(
            f"Converting {len(paths)} file"
            + ("" if len(paths) == 1 else "s")
            + "…"
        )
        self._active_token = token

        thread = threading.Thread(
            target=self._conversion_worker,
            args=(paths, token),
            daemon=True,
        )
        thread.start()

    def _conversion_worker(
        self, paths: list[Path], token: CancelToken
    ) -> None:
        try:
            job = DownsampleJob()
            summary = job.run(
                paths,
                on_log=lambda message: dispatch(self.log_view.add_line, message),
                on_progress=lambda current, total: dispatch(
                    self.task_bar.update, current, total
                ),
                cancel=token,
            )
            dispatch(self._on_conversion_finished, summary)
        except Exception as exc:
            dispatch(self._on_task_error, str(exc))

    def _on_conversion_finished(self, summary) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()

        if summary.errors:
            show_alert(
                self.get_root(),
                "Some Files Could Not Be Converted",
                "\n".join(summary.errors[:6]),
            )

        if summary.cancelled:
            self.log_view.add_line("Cancelled.")
            self._toast("Cancelled.")
        else:
            self.log_view.add_line(
                f"Finished: {summary.converted} converted, "
                f"{summary.skipped} skipped."
            )
            if summary.converted:
                self._toast(
                    f"Converted {summary.converted} file"
                    + ("" if summary.converted == 1 else "s")
                    + " to 16-bit."
                )
            elif summary.errors:
                self._toast(
                    f"No files converted; {len(summary.errors)} failed."
                )
            else:
                self._toast("Nothing to convert; see the log for details.")

        self._update_controls()
        if self._album is not None:
            self._on_album_selected(self._album)

    def _on_task_error(self, message: str) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()
        self._update_controls()
        show_alert(self.get_root(), "Something Went Wrong", message)

    def _toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    def shutdown(self) -> None:
        self._busy = True
        if self._active_token is not None:
            self._active_token.cancel()
