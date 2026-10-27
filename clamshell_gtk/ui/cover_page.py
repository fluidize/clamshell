"""Cover Converter page: PNG artwork to JPEG."""

from __future__ import annotations

import threading
from pathlib import Path

from gi.repository import Adw, Gtk

from ..core.cover import convert_covers
from .common import (
    CancelToken,
    FolderChooserRow,
    LogView,
    TaskBar,
    choose_folder,
    dispatch,
    show_alert,
)


class CoverPage(Gtk.Box):
    def __init__(self, toast_overlay: Adw.ToastOverlay) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._toast_overlay = toast_overlay
        self._root: Path | None = None
        self._busy = False
        self._active_token: CancelToken | None = None
        self._build()

    def _build(self) -> None:
        scroller = Gtk.ScrolledWindow(
            vexpand=True,
            hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        clamp.set_vexpand(True)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=24,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )

        title = Gtk.Label(label="Cover Converter", xalign=0)
        title.add_css_class("title-2")
        content.append(title)

        description = Gtk.Label(
            label=(
                "Replace embedded PNG artwork with JPEG in every .flac and "
                ".mp3 file in a folder, including subfolders."
            ),
            xalign=0,
            wrap=True,
        )
        description.add_css_class("dim-label")
        content.append(description)

        source_group = Adw.PreferencesGroup(title="Source")
        self.folder_row = FolderChooserRow(self._choose_folder)
        source_group.add(self.folder_row)
        content.append(source_group)

        options_group = Adw.PreferencesGroup(title="Output")
        adjustment = Gtk.Adjustment(
            value=100,
            lower=1,
            upper=100,
            step_increment=1,
            page_increment=10,
        )
        self.quality_row = Adw.SpinRow(
            title="JPEG Quality",
            subtitle=(
                "JPEG is lossy. 100 with no chroma subsampling gives the "
                "highest quality and the largest files."
            ),
            adjustment=adjustment,
            digits=0,
            numeric=True,
        )
        self.quality_row.set_value(100)
        options_group.add(self.quality_row)
        content.append(options_group)

        action_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.END,
        )
        self.convert_button = Gtk.Button(label="Convert Covers")
        self.convert_button.add_css_class("suggested-action")
        self.convert_button.set_sensitive(False)
        self.convert_button.connect(
            "clicked", lambda _button: self._start_conversion()
        )
        action_bar.append(self.convert_button)
        content.append(action_bar)

        self.task_bar = TaskBar()
        content.append(self.task_bar)

        self.log_view = LogView(title="Log", min_height=180)
        self.log_view.set_vexpand(True)
        content.append(self.log_view)

        clamp.set_child(content)
        scroller.set_child(clamp)
        self.append(scroller)

    def _choose_folder(self) -> None:
        root = self.get_root()
        if root is None:
            return
        choose_folder(root, self._set_folder)

    def open_folder(self) -> None:
        """Entry point for the window's Ctrl+O action."""
        self._choose_folder()

    def _set_folder(self, folder: Path) -> None:
        if self._busy:
            return
        self._root = folder
        self.folder_row.set_folder(folder)
        self.convert_button.set_sensitive(True)

    def _start_conversion(self) -> None:
        if self._busy or self._root is None:
            return

        self._busy = True
        self.convert_button.set_sensitive(False)
        self.folder_row.set_sensitive(False)
        self.quality_row.set_sensitive(False)
        self.log_view.add_line(f"Scanning {self._root}…")

        token = self.task_bar.start("Converting covers…")
        self._active_token = token
        quality = int(self.quality_row.get_value())

        thread = threading.Thread(
            target=self._conversion_worker,
            args=(self._root, quality, token),
            daemon=True,
        )
        thread.start()

    def _conversion_worker(
        self, root: Path, quality: int, token: CancelToken
    ) -> None:
        try:
            result = convert_covers(
                root,
                quality=quality,
                on_log=lambda message: dispatch(self.log_view.add_line, message),
                on_progress=lambda current, total: dispatch(
                    self.task_bar.update, current, total
                ),
                cancel=token,
            )
            dispatch(self._on_conversion_finished, result)
        except Exception as exc:
            dispatch(self._on_task_error, str(exc))

    def _on_conversion_finished(self, result) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()
        self.convert_button.set_sensitive(self._root is not None)
        self.folder_row.set_sensitive(True)
        self.quality_row.set_sensitive(True)

        if result.errors:
            self.log_view.add_line(
                f"Finished with {len(result.errors)} error(s)."
            )
            show_alert(
                self.get_root(),
                "Some Covers Could Not Be Converted",
                "\n".join(result.errors[:6]),
            )
        elif result.cancelled:
            self.log_view.add_line("Cancelled.")
        else:
            self.log_view.add_line(
                f"Finished: {result.converted} file(s) converted, "
                f"{result.unchanged} unchanged."
            )

        if result.cancelled:
            self._toast("Cancelled.")
        elif result.errors:
            self._toast(
                f"Converted {result.converted} file(s); "
                f"{len(result.errors)} failed."
            )
        elif result.converted:
            self._toast(
                f"Converted covers in {result.converted} file"
                + ("" if result.converted == 1 else "s")
                + "."
            )
        else:
            self._toast("No PNG artwork found.")

    def _on_task_error(self, message: str) -> None:
        self._busy = False
        self._active_token = None
        self.task_bar.finish()
        self.convert_button.set_sensitive(self._root is not None)
        self.folder_row.set_sensitive(True)
        self.quality_row.set_sensitive(True)
        show_alert(self.get_root(), "Something Went Wrong", message)

    def _toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    def shutdown(self) -> None:
        self._busy = True
        if self._active_token is not None:
            self._active_token.cancel()
