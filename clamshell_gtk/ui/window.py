"""Main application window."""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .. import APP_ICON, APP_NAME
from .cover_page import CoverPage
from .downsample_page import DownsamplePage
from .playlist_page import PlaylistPage


class ClamshellWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title(APP_NAME)
        self.set_default_size(1020, 720)
        self.set_size_request(560, 420)
        self.set_icon_name(APP_ICON)

        self.toast_overlay = Adw.ToastOverlay()
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)

        self.playlist_page = PlaylistPage(self.toast_overlay)
        self.cover_page = CoverPage(self.toast_overlay)
        self.downsample_page = DownsamplePage(self.toast_overlay)
        self._pages = (
            self.playlist_page,
            self.cover_page,
            self.downsample_page,
        )

        self.view_stack.add_titled_with_icon(
            self.playlist_page,
            "playlist",
            "Playlist Organizer",
            "view-list-symbolic",
        )
        self.view_stack.add_titled_with_icon(
            self.cover_page,
            "covers",
            "Cover Converter",
            "image-x-generic-symbolic",
        )
        self.view_stack.add_titled_with_icon(
            self.downsample_page,
            "downsample",
            "Downsampler",
            "audio-x-generic-symbolic",
        )

        header = Adw.HeaderBar()
        self.view_switcher = Adw.ViewSwitcher(stack=self.view_stack)
        self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.view_switcher)

        menu = Gio.Menu()
        menu.append("Open Music Folder", "win.open-folder")
        menu.append("About Clamshell", "app.about")
        menu.append("Quit", "app.quit")
        self.menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=menu,
            tooltip_text="Main Menu",
        )
        header.pack_end(self.menu_button)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.view_stack)
        self.view_switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        toolbar_view.add_bottom_bar(self.view_switcher_bar)

        self.toast_overlay.set_child(toolbar_view)
        self.set_content(self.toast_overlay)

        self._install_actions()
        self._install_breakpoints()
        self.connect("close-request", self._on_close_request)

    def _install_actions(self) -> None:
        open_action = Gio.SimpleAction(name="open-folder")
        open_action.connect("activate", lambda *_args: self._open_folder())
        self.add_action(open_action)

        close_action = Gio.SimpleAction(name="close")
        close_action.connect("activate", lambda *_args: self.close())
        self.add_action(close_action)

        for action_name, view_name in (
            ("view-playlist", "playlist"),
            ("view-covers", "covers"),
            ("view-downsample", "downsample"),
        ):
            action = Gio.SimpleAction(name=action_name)
            action.connect(
                "activate",
                lambda _action, _param, name=view_name: (
                    self.view_stack.set_visible_child_name(name)
                ),
            )
            self.add_action(action)

    def _install_breakpoints(self) -> None:
        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 720sp")
        )
        breakpoint.add_setter(self.view_switcher, "visible", False)
        breakpoint.add_setter(self.view_switcher_bar, "reveal", True)
        breakpoint.add_setter(self.playlist_page.split, "collapsed", True)
        breakpoint.add_setter(self.downsample_page.split, "collapsed", True)
        self.add_breakpoint(breakpoint)

    def _open_folder(self) -> None:
        page = self.view_stack.get_visible_child()
        opener = getattr(page, "open_folder", None)
        if opener is not None:
            opener()

    def _on_close_request(self, _window) -> bool:
        for page in self._pages:
            page.shutdown()
        return False
