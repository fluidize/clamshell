"""Playlist Organizer page: reorder, retag and copy tracks."""

from __future__ import annotations

import threading
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango

from ..core.audio import (
    MetadataCache,
    album_directories,
    extract_art,
    format_file_label,
    list_audio_files,
    track_sort_key,
)
from ..core.playlist import add_to_playlist, apply_album
from .common import (
    AlbumList,
    CancelToken,
    TaskBar,
    choose_folder,
    dispatch,
    pil_to_png_bytes,
    show_alert,
    texture_from_png_bytes,
)


class TrackRow(Adw.ActionRow):
    def __init__(self, file_path: Path, title: str, detail: str) -> None:
        super().__init__(title=title, subtitle=detail)
        self.path = str(file_path)
        self.set_activatable(False)

        handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
        handle.set_valign(Gtk.Align.CENTER)
        handle.add_css_class("dim-label")
        self.add_suffix(handle)


class AddToPlaylistDialog(Adw.Dialog):
    def __init__(self, root: Path, current_album: Path, on_accept) -> None:
        super().__init__()
        self.set_title("Add to Playlist")
        self.set_content_width(440)
        self.set_content_height(500)
        self._root = root
        self._current_album = current_album
        self._on_accept = on_accept
        self._target: Path | None = None
        self._syncing = False

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Adw.WindowTitle(title="Add to Playlist"))

        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.add_css_class("flat")
        cancel_button.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel_button)

        self.add_button = Gtk.Button(label="Add")
        self.add_button.add_css_class("suggested-action")
        self.add_button.set_sensitive(False)
        self.add_button.connect("clicked", self._on_add_clicked)
        header.pack_end(self.add_button)
        toolbar.add_top_bar(header)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=6,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        description = Gtk.Label(
            label=(
                "Choose a playlist folder next to this album, or type a "
                "new folder name."
            ),
            xalign=0,
            wrap=True,
        )
        description.add_css_class("dim-label")
        content.append(description)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_row_selected)
        self._populate_playlists()

        scroller = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroller.set_child(self.listbox)
        content.append(scroller)

        self.name_entry = Gtk.Entry(
            placeholder_text="New playlist folder name",
            hexpand=True,
        )
        self.name_entry.connect("changed", self._on_name_changed)
        self.name_entry.connect("activate", lambda _entry: self._on_add_clicked())
        content.append(self.name_entry)

        hint = Gtk.Label(
            label="The folder will be created if it does not exist.",
            xalign=0,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        content.append(hint)

        toolbar.set_content(content)
        self.set_child(toolbar)

    def _populate_playlists(self) -> None:
        try:
            entries = sorted(
                self._root.iterdir(), key=lambda item: item.name.lower()
            )
        except OSError:
            entries = []

        found = False
        for entry in entries:
            if (
                not entry.is_dir()
                or entry == self._current_album
                or entry.name.startswith(".")
            ):
                continue
            found = True
            count = len(list_audio_files(entry))
            subtitle = (
                "Empty folder"
                if count == 0
                else ("1 track" if count == 1 else f"{count} tracks")
            )
            row = Adw.ActionRow(title=entry.name, subtitle=subtitle)
            row.folder_path = entry
            self.listbox.append(row)

        if not found:
            placeholder = Adw.ActionRow(title="No other folders here")
            placeholder.set_activatable(False)
            placeholder.set_selectable(False)
            placeholder.add_css_class("dim-label")
            self.listbox.append(placeholder)

    def _on_row_selected(self, _listbox, row) -> None:
        folder = getattr(row, "folder_path", None)
        if folder is None:
            return
        self._target = folder
        self._syncing = True
        self.name_entry.set_text("")
        self.name_entry.remove_css_class("error")
        self._syncing = False
        self.add_button.set_sensitive(True)

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        if self._syncing:
            return

        self._syncing = True
        self.listbox.unselect_all()
        self._syncing = False

        name = entry.get_text().strip()
        invalid = (
            not name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
            or name.startswith(".")
        )
        if invalid:
            self._target = None
            self.add_button.set_sensitive(False)
            if name:
                entry.add_css_class("error")
            else:
                entry.remove_css_class("error")
            return

        entry.remove_css_class("error")
        self._target = self._root / name
        self.add_button.set_sensitive(True)

    def _on_add_clicked(self) -> None:
        target = self._target
        if target is None:
            return
        if target == self._current_album:
            show_alert(
                self,
                "Choose Another Folder",
                "Choose a different playlist folder.",
            )
            return
        if target.exists() and not target.is_dir():
            show_alert(
                self,
                "Name Already in Use",
                "A file with this name already exists.",
            )
            return
        self.close()
        self._on_accept(target)


class PlaylistPage(Gtk.Box):
    def __init__(self, toast_overlay: Adw.ToastOverlay) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._toast_overlay = toast_overlay
        self._cache = MetadataCache()
        self._root: Path | None = None
        self._album: Path | None = None
        self._busy = False
        self._load_generation = 0
        self._drag_row: TrackRow | None = None
        self._highlight_row: Gtk.ListBoxRow | None = None
        self._active_token: CancelToken | None = None
        self._build()

    # -- construction -------------------------------------------------

    def _build(self) -> None:
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
                "Open a folder of albums to start organizing your tracks.",
                "Open Music Folder",
                self.open_folder,
            ),
            "no-folder",
        )
        self.stack.add_named(
            self._make_status_page(
                "folder-music-symbolic",
                "No Albums Found",
                "This folder has no subfolders containing .flac or .mp3 files.",
                "Choose Another Folder",
                self.open_folder,
            ),
            "empty-folder",
        )
        self.stack.add_named(
            self._make_status_page(
                "audio-x-generic-symbolic",
                "Select an Album",
                "Pick an album from the sidebar to see its tracks.",
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
            spacing=10,
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

        page.append(header)

        self.art_picture = Gtk.Picture(
            can_shrink=True,
            content_fit=Gtk.ContentFit.COVER,
            hexpand=True,
            vexpand=True,
        )
        self.art_picture.set_opacity(0.12)

        self.track_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            show_separators=False,
        )
        self.track_list.set_activate_on_single_click(False)
        self.track_list.add_css_class("track-list")

        scroller = Gtk.ScrolledWindow(
            vexpand=True,
            hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroller.set_child(self.track_list)
        scroller.add_css_class("track-scroller")

        overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        overlay.set_child(self.art_picture)
        overlay.add_overlay(scroller)
        overlay.add_css_class("art-background")
        page.append(overlay)

        self._install_drop_target()
        page.append(self._build_action_bar())

        self.task_bar = TaskBar()
        page.append(self.task_bar)
        return page

    def _build_action_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            valign=Gtk.Align.CENTER,
        )
        bar.add_css_class("action-bar")

        self.album_entry = Gtk.Entry(
            placeholder_text="Album title",
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self.album_entry.connect("activate", lambda _entry: self._apply())
        bar.append(self.album_entry)

        self.add_button = Gtk.Button(
            label="Add to Playlist…",
            valign=Gtk.Align.CENTER,
        )
        self.add_button.set_sensitive(False)
        self.add_button.connect("clicked", lambda _button: self._add_selected())
        bar.append(self.add_button)

        self.apply_button = Gtk.Button(
            label="Apply",
            valign=Gtk.Align.CENTER,
        )
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.set_sensitive(False)
        self.apply_button.set_tooltip_text(
            "Renumber tracks in the current order and rename files"
        )
        self.apply_button.connect("clicked", lambda _button: self._apply())
        bar.append(self.apply_button)
        return bar

    # -- drag and drop reordering -------------------------------------

    def _install_drop_target(self) -> None:
        target = Gtk.DropTarget.new(
            GObject.TYPE_STRING, Gdk.DragAction.MOVE
        )
        target.connect("motion", self._on_drag_motion)
        target.connect("leave", self._on_drag_leave)
        target.connect("drop", self._on_drop)
        self.track_list.add_controller(target)

    def _add_drag_source(self, row: TrackRow) -> None:
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect(
            "prepare", lambda _source, _x, _y, r=row: self._prepare_drag(r)
        )
        source.connect(
            "drag-begin", lambda _source, _drag, r=row: self._begin_drag(r)
        )
        source.connect(
            "drag-end",
            lambda _source, _drag, _delete, r=row: self._end_drag(r),
        )
        row._drag_source = source
        row.add_controller(source)

    def _prepare_drag(self, row: TrackRow):
        if self._busy:
            return None
        self._drag_row = row
        return Gdk.ContentProvider.new_for_bytes(
            "text/plain", GLib.Bytes.new(str(row.path).encode())
        )

    def _begin_drag(self, row: TrackRow) -> None:
        try:
            source = getattr(row, "_drag_source", None)
            if source is not None:
                source.set_icon(Gtk.WidgetPaintable.new(row), 0, 0)
        except Exception:
            pass

    def _end_drag(self, _row: TrackRow) -> None:
        self._drag_row = None
        self._clear_highlight()

    def _on_drag_motion(self, _target, x: float, y: float):
        row, above = self._row_at_position(x, y)
        if row is None:
            self._clear_highlight()
            return Gdk.DragAction.MOVE

        if self._highlight_row is not row:
            self._clear_highlight()
            self._highlight_row = row
        row.remove_css_class("drop-above")
        row.remove_css_class("drop-below")
        row.add_css_class("drop-above" if above else "drop-below")
        return Gdk.DragAction.MOVE

    def _on_drag_leave(self, _target) -> None:
        self._clear_highlight()

    def _on_drop(self, _target, _value, x: float, y: float) -> bool:
        row = self._drag_row
        if row is None:
            return False

        target_row, above = self._row_at_position(x, y)
        if target_row is None:
            target_index = self._row_count()
        else:
            target_index = target_row.get_index() + (0 if above else 1)

        source_index = row.get_index()
        self._clear_highlight()
        GLib.idle_add(self._move_row, row, source_index, target_index)
        return True

    def _move_row(
        self, row: TrackRow, source_index: int, target_index: int
    ) -> bool:
        if source_index < 0:
            return False
        if source_index < target_index:
            target_index -= 1
        self.track_list.remove(row)
        target_index = max(
            0, min(target_index, self._row_count())
        )
        self.track_list.insert(row, target_index)
        self.track_list.unselect_all()
        self.track_list.select_row(row)
        row.grab_focus()
        self._drag_row = None
        return False

    def _row_at_position(
        self, _x: float, y: float
    ) -> tuple[Gtk.ListBoxRow | None, bool]:
        row = self.track_list.get_row_at_y(int(y))
        if row is None:
            return None, False
        allocation = row.get_allocation()
        return row, y < allocation.y + allocation.height / 2

    def _clear_highlight(self) -> None:
        if self._highlight_row is not None:
            self._highlight_row.remove_css_class("drop-above")
            self._highlight_row.remove_css_class("drop-below")
            self._highlight_row = None

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
        self._cache.clear()
        self._clear_tracks()
        self.art_picture.set_paintable(None)
        self.album_title.set_text("")
        self.album_count.set_text("")
        self._update_controls(loaded=False)
        albums = album_directories(folder)

        if not albums:
            self.album_list.clear()
            self.stack.set_visible_child_name("empty-folder")
            return

        self.stack.set_visible_child_name("no-album")
        self.album_list.set_albums(albums)

    def _on_album_selected(self, album: Path) -> None:
        if self._busy:
            return
        self._album = album
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self._load_album(album)

    def _load_album(self, album: Path) -> None:
        self._load_generation += 1
        generation = self._load_generation

        self.album_title.set_text(album.name)
        self.album_count.set_text("Loading…")
        self.album_entry.set_text("")
        self.art_picture.set_paintable(None)
        self._clear_tracks()
        self._add_placeholder("Loading tracks…")
        self.stack.set_visible_child_name("album")
        self._update_controls(loaded=False)

        thread = threading.Thread(
            target=self._load_album_worker,
            args=(album, generation),
            daemon=True,
        )
        thread.start()

    def _load_album_worker(self, album: Path, generation: int) -> None:
        try:
            entries = [
                (
                    file_path,
                    self._cache.get(file_path),
                    format_file_label(file_path)[1],
                )
                for file_path in list_audio_files(album)
            ]
            entries.sort(key=lambda entry: track_sort_key((entry[0], entry[1])))

            art_bytes = None
            for file_path, _meta, _detail in entries:
                image = extract_art(file_path)
                if image is not None:
                    art_bytes = pil_to_png_bytes(image)
                    break

            dispatch(
                self._on_tracks_loaded, album, entries, art_bytes, generation
            )
        except Exception as exc:
            dispatch(self._on_load_error, album, str(exc), generation)

    def _on_tracks_loaded(
        self, album: Path, entries: list, art_bytes, generation: int
    ) -> None:
        if album != self._album or generation != self._load_generation:
            return

        self._clear_tracks()
        if not entries:
            self._add_placeholder("No .flac or .mp3 files found in this folder")
            self.album_count.set_text("No tracks")
            self._update_controls(loaded=False)
            return

        for file_path, meta, detail in entries:
            row = TrackRow(file_path, meta["title"], detail)
            self._add_drag_source(row)
            self.track_list.append(row)

        count = len(entries)
        self.album_count.set_text(
            "1 track" if count == 1 else f"{count} tracks"
        )
        album_tag = next(
            (meta["album"] for _file, meta, _detail in entries if meta["album"]),
            album.name,
        )
        self.album_entry.set_text(album_tag)
        self.art_picture.set_paintable(
            texture_from_png_bytes(art_bytes)
        )
        self._update_controls(loaded=True)

    def _on_load_error(
        self, album: Path, message: str, generation: int
    ) -> None:
        if album != self._album or generation != self._load_generation:
            return
        self._clear_tracks()
        self._add_placeholder("Could not read this album")
        self.album_count.set_text("—")
        self._update_controls(loaded=False)
        self._toast(f"Could not read album: {message}")

    # -- track list helpers -------------------------------------------

    def _clear_tracks(self) -> None:
        self.track_list.remove_all()
        self._highlight_row = None

    def _add_placeholder(self, text: str) -> None:
        row = Adw.ActionRow(title=text)
        row.set_activatable(False)
        row.set_selectable(False)
        row.add_css_class("dim-label")
        self.track_list.append(row)

    def _track_rows(self):
        index = 0
        while True:
            row = self.track_list.get_row_at_index(index)
            if row is None:
                break
            if getattr(row, "path", None):
                yield row
            index += 1

    def _row_count(self) -> int:
        count = 0
        while self.track_list.get_row_at_index(count) is not None:
            count += 1
        return count

    def _selected_paths(self) -> list[Path]:
        return [
            Path(row.path)
            for row in self._track_rows()
            if row.is_selected()
        ]

    def _update_controls(self, loaded: bool) -> None:
        has_tracks = any(True for _row in self._track_rows())
        enabled = loaded and has_tracks and not self._busy
        self.apply_button.set_sensitive(enabled)
        self.add_button.set_sensitive(enabled)
        self.album_entry.set_sensitive(loaded and not self._busy)
        self.track_list.set_sensitive(not self._busy)
        self.album_list.set_sensitive(not self._busy)

    # -- apply / copy --------------------------------------------------

    def _apply(self) -> None:
        if self._busy or self._album is None:
            return

        paths = [Path(row.path) for row in self._track_rows()]
        if not paths:
            self._toast("Select an album with tracks first.")
            return

        title = self.album_entry.get_text().strip()
        self._busy = True
        self._update_controls(loaded=True)
        token = self.task_bar.start(
            f"Applying tags to {len(paths)} track"
            + ("" if len(paths) == 1 else "s")
            + "…"
        )
        self._active_token = token

        thread = threading.Thread(
            target=self._apply_worker,
            args=(paths, title, token),
            daemon=True,
        )
        thread.start()

    def _apply_worker(
        self, paths: list[Path], title: str, token: CancelToken
    ) -> None:
        try:
            result = apply_album(
                paths,
                title,
                on_progress=lambda current, total: dispatch(
                    self.task_bar.update, current, total
                ),
                on_item=lambda index, new_title, new_path: dispatch(
                    self._on_item_updated, index, new_title, new_path
                ),
                cancel=token,
            )
            dispatch(self._on_apply_finished, result)
        except Exception as exc:
            dispatch(self._on_task_error, str(exc))

    def _on_item_updated(
        self, index: int, new_title: str, new_path: str
    ) -> None:
        row = self.track_list.get_row_at_index(index)
        if row is not None and getattr(row, "path", None):
            row.set_title(new_title)
            row.path = new_path

    def _on_apply_finished(self, result) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()
        self._update_controls(loaded=True)

        if result.errors:
            show_alert(
                self.get_root(),
                "Some Tracks Could Not Be Updated",
                "\n".join(result.errors[:6]),
            )
        if result.cancelled:
            self._toast("Cancelled.")
        elif result.errors:
            self._toast(
                f"Updated {result.applied} track(s); "
                f"{len(result.errors)} failed."
            )
        else:
            self._toast(
                f"Updated {result.applied} track"
                + ("" if result.applied == 1 else "s")
                + "."
            )

        if self._album is not None:
            self._load_album(self._album)

    def _add_selected(self) -> None:
        if self._busy or self._album is None:
            return

        selected = self._selected_paths()
        if not selected:
            self._toast("Select one or more songs to add.")
            return

        dialog = AddToPlaylistDialog(
            self._album.parent,
            self._album,
            on_accept=lambda target: self._copy_to(target, selected),
        )
        dialog.present(self.get_root())

    def _copy_to(self, target: Path, sources: list[Path]) -> None:
        if self._busy:
            return
        if target == self._album:
            self._toast("Choose a different playlist folder.")
            return

        self._busy = True
        self._update_controls(loaded=True)
        token = self.task_bar.start(
            f"Adding {len(sources)} song"
            + ("" if len(sources) == 1 else "s")
            + "…"
        )
        self._active_token = token

        thread = threading.Thread(
            target=self._copy_worker,
            args=(target, sources, token),
            daemon=True,
        )
        thread.start()

    def _copy_worker(
        self, target: Path, sources: list[Path], token: CancelToken
    ) -> None:
        try:
            result = add_to_playlist(
                sources,
                target,
                on_progress=lambda current, total: dispatch(
                    self.task_bar.update, current, total
                ),
                cancel=token,
            )
            dispatch(self._on_copy_finished, target, result)
        except Exception as exc:
            dispatch(self._on_task_error, str(exc))

    def _on_copy_finished(self, target: Path, result) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()
        self._update_controls(loaded=True)

        if result.errors:
            show_alert(
                self.get_root(),
                "Some Songs Could Not Be Added",
                "\n".join(result.errors[:6]),
            )
        if result.cancelled:
            self._toast("Cancelled.")
            return

        message = (
            f"Added {result.copied} song"
            + ("" if result.copied == 1 else "s")
            + f" to '{target.name}'."
            if result.copied
            else "No new songs added."
        )
        if result.skipped:
            message += f" {result.skipped} already existed."
        self._toast(message)

    def _on_task_error(self, message: str) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()
        self._update_controls(loaded=True)
        show_alert(self.get_root(), "Something Went Wrong", message)

    def _toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    def shutdown(self) -> None:
        """Called when the window closes; stop accepting new work."""
        self._busy = True
        if self._active_token is not None:
            self._active_token.cancel()
