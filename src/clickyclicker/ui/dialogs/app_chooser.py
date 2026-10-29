"""Choosing an installed application to open."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from ...models import AppRef
from ..widgets.rows import plain

__all__ = ["AppChooserDialog", "choose_app"]


def _launchable_apps() -> list[Gio.AppInfo]:
    """Apps a person would expect to see in a launcher, sorted by name.

    Terminal-only programs are left out: started from a button they would run
    with no terminal to show them, and appear to do nothing.
    """
    apps = []
    for app in Gio.AppInfo.get_all():
        if not app.should_show() or not app.get_id():
            continue
        if isinstance(app, Gio.DesktopAppInfo) and app.get_boolean("Terminal"):
            continue
        apps.append(app)
    apps.sort(key=lambda a: (a.get_display_name() or "").casefold())
    return apps


class AppChooserDialog(Adw.Dialog):
    """Modal list of installed applications, with search."""

    def __init__(self, on_chosen: Callable[[AppRef], None], selected: AppRef | None = None) -> None:
        super().__init__()
        self.set_title("Choose Application")
        self.set_content_width(480)
        self.set_content_height(600)
        self._on_chosen = on_chosen
        self._rows: list[tuple[Adw.ActionRow, str]] = []

        self._search = Gtk.SearchEntry(placeholder_text="Search applications")
        self._search.connect("search-changed", lambda *_: self._apply_filter())

        group = Adw.PreferencesGroup()
        for app in _launchable_apps():
            name = app.get_display_name() or app.get_id()
            row = plain(Adw.ActionRow(), title=name, subtitle=app.get_description() or "")
            row.set_subtitle_lines(1)
            icon = Gtk.Image.new_from_gicon(app.get_icon()) if app.get_icon() else Gtk.Image(
                icon_name="application-x-executable-symbolic"
            )
            icon.set_icon_size(Gtk.IconSize.LARGE)
            row.add_prefix(icon)
            if selected is not None and selected.id == app.get_id():
                row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
            row.set_activatable(True)
            row.connect("activated", self._on_activated, app)
            group.add(row)
            self._rows.append((row, f"{name} {app.get_id()}".casefold()))

        page = Adw.PreferencesPage()
        page.add(group)

        self._empty = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title="No Matches",
            description="No installed application matches your search.",
        )
        self._stack = Gtk.Stack()
        self._stack.add_named(page, "list")
        self._stack.add_named(self._empty, "empty")

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Choose Application"))

        search_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_bar.set_margin_top(6)
        search_bar.set_margin_bottom(6)
        search_bar.set_margin_start(12)
        search_bar.set_margin_end(12)
        search_bar.append(self._search)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(search_bar)
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    def _apply_filter(self) -> None:
        needle = self._search.get_text().strip().casefold()
        matches = 0
        for row, haystack in self._rows:
            visible = not needle or needle in haystack
            row.set_visible(visible)
            matches += visible
        self._stack.set_visible_child_name("list" if matches else "empty")

    def _on_activated(self, _row: Adw.ActionRow, app: Gio.AppInfo) -> None:
        chosen = AppRef(
            id=app.get_id() or "",
            name=app.get_display_name() or "",
            path=(app.get_filename() or "") if isinstance(app, Gio.DesktopAppInfo) else "",
        )
        self.close()
        # Deferred so a caller that re-renders in response does so after this
        # dialog has gone, as with the key chooser.
        GLib.idle_add(lambda: (self._on_chosen(chosen), GLib.SOURCE_REMOVE)[1])


def choose_app(
    parent: Gtk.Widget, on_chosen: Callable[[AppRef], None], selected: AppRef | None = None
) -> AppChooserDialog:
    """Open the application chooser anchored to *parent*."""
    dialog = AppChooserDialog(on_chosen, selected)
    dialog.present(parent)
    return dialog
