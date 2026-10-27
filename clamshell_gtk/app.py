"""Application entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ICON, APP_ID, APP_NAME, __version__
from .ui.window import ClamshellWindow

logger = logging.getLogger(__name__)


class ClamshellApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

    def _on_startup(self, _app) -> None:
        GLib.set_application_name(APP_NAME)
        self._load_css()
        self._install_actions()
        self._install_accels()

    def _load_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return

        provider = Gtk.CssProvider()

        def on_error(_provider, section, error) -> None:
            logger.warning(
                "CSS error at %s: %s", section.to_string(), error.message
            )

        provider.connect("parsing-error", on_error)
        try:
            provider.load_from_path(str(Path(__file__).parent / "style.css"))
        except GLib.Error as exc:
            logger.warning("Could not load stylesheet: %s", exc.message)
            return

        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _install_actions(self) -> None:
        about_action = Gio.SimpleAction(name="about")
        about_action.connect("activate", lambda *_args: self._show_about())
        self.add_action(about_action)

        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_args: self.quit())
        self.add_action(quit_action)

    def _install_accels(self) -> None:
        accels = {
            "win.open-folder": ["<Control>o"],
            "win.close": ["<Control>w"],
            "win.view-playlist": ["<Control>1"],
            "win.view-covers": ["<Control>2"],
            "win.view-downsample": ["<Control>3"],
            "app.quit": ["<Control>q"],
        }
        for action, shortcuts in accels.items():
            self.set_accels_for_action(action, shortcuts)

    def _show_about(self) -> None:
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ICON,
            developers=["fluidize"],
            version=__version__,
            comments=(
                "A collection of tools for .flac and .mp3 music libraries: "
                "playlist organizing, cover art conversion, and downsampling "
                "high-resolution FLAC albums to 16-bit."
            ),
            website="https://github.com/fluidize/clamshell",
            issue_url="https://github.com/fluidize/clamshell/issues",
        )
        dialog.present(self.props.active_window)

    def _on_activate(self, _app) -> None:
        window = self.props.active_window
        if window is None:
            window = ClamshellWindow(application=self)
        window.present()

    def _on_shutdown(self, _app) -> None:
        for window in self.get_windows():
            for page in getattr(window, "_pages", ()):
                page.shutdown()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = ClamshellApplication()
    return app.run(argv if argv is not None else sys.argv)
