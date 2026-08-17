"""The main window.

An ``AdwNavigationSplitView`` with a section list on the left and the selected
section on the right.  The Macros section nests an ``AdwNavigationView`` so that
opening a macro pushes the editor with proper back navigation, rather than
replacing the list and losing the user's place.

The window also owns the status poll: it asks the daemon what is running once a
second while it is visible, which is what drives the running indicators and the
Stop button.  Polling stops when the window is not mapped, so a backgrounded
window costs nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ..models import Macro
from ..services import MacroLibrary
from ..services.daemon_client import DaemonStatus
from .dialogs.position_picker import desktop_size
from .dialogs.preferences import PreferencesDialog
from .dialogs.recorder import record_macro
from .pages.devices import DevicesPage
from .pages.editor import MacroEditorPage
from .pages.macros import MacrosPage
from .pages.mappings import MappingsPage
from .widgets.rows import plain

__all__ = ["MainWindow"]

#: How often to ask the daemon what is running, while the window is visible.
_POLL_INTERVAL_MS = 1000

_SECTIONS: list[tuple[str, str, str]] = [
    ("macros", "Macros", "view-list-symbolic"),
    ("mappings", "Mappings", "input-keyboard-symbolic"),
    ("status", "Status", "emblem-system-symbolic"),
]


class MainWindow(Adw.ApplicationWindow):
    """Top-level window."""

    __gtype_name__ = "ClickyMainWindow"

    def __init__(
        self,
        application: Adw.Application,
        library: MacroLibrary,
        gsettings: Gio.Settings | None = None,
    ) -> None:
        super().__init__(application=application)
        self._library = library
        self._gsettings = gsettings
        self._editor: MacroEditorPage | None = None
        self._poll_source: int | None = None

        self.set_title("Clicky Clicker")
        self.set_default_size(1080, 760)
        self.set_size_request(400, 500)

        self._toasts = Adw.ToastOverlay()
        self._build()
        self._install_actions()
        self._restore_geometry()

        self.connect("map", lambda *_: self._start_polling())
        self.connect("unmap", lambda *_: self._stop_polling())
        self.connect("close-request", self._on_close)

        self._report_screen_size()
        self._report_load_problems()

    # --- Construction ---------------------------------------------------

    def _build(self) -> None:
        self._macros_page = MacrosPage(
            self._library,
            on_open=self.open_macro,
            on_test=self.test_macro,
            on_stop=self.stop_all,
            on_toast=self.toast,
        )
        self._macros_navigation = Adw.NavigationView()
        self._macros_navigation.add(self._macros_page)
        self._macros_navigation.connect("popped", self._on_editor_popped)

        self._mappings_navigation = Adw.NavigationView()
        self._mappings_navigation.add(MappingsPage(self._library, on_toast=self.toast))

        self._status_page = DevicesPage(self._library, on_toast=self.toast)
        self._status_navigation = Adw.NavigationView()
        self._status_navigation.add(self._status_page)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.add_named(self._macros_navigation, "macros")
        self._content_stack.add_named(self._mappings_navigation, "mappings")
        self._content_stack.add_named(self._status_navigation, "status")

        self._split = Adw.NavigationSplitView()
        self._split.set_sidebar(self._build_sidebar())
        self._split.set_content(
            Adw.NavigationPage(title="Clicky Clicker", child=self._content_stack)
        )
        self._split.set_min_sidebar_width(220)
        self._split.set_max_sidebar_width(280)

        self._toasts.set_child(self._split)
        self.set_content(self._toasts)

    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Clicky Clicker"))

        menu = Gio.Menu()
        primary = Gio.Menu()
        primary.append("Stop All Macros", "win.stop-all")
        primary.append("Preferences", "app.preferences")
        menu.append_section(None, primary)
        about = Gio.Menu()
        about.append("Keyboard Shortcuts", "win.shortcuts")
        about.append("About Clicky Clicker", "app.about")
        menu.append_section(None, about)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        menu_button.set_tooltip_text("Main menu")
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Main menu"])
        header.pack_end(menu_button)

        self._sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._sidebar_list.add_css_class("navigation-sidebar")
        for identifier, title, icon_name in _SECTIONS:
            row = Adw.ActionRow(title=title)
            row.add_prefix(Gtk.Image(icon_name=icon_name))
            row.set_name(identifier)
            self._sidebar_list.append(row)
        self._sidebar_list.connect("row-selected", self._on_section_selected)
        self._sidebar_list.select_row(self._sidebar_list.get_row_at_index(0))

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(Gtk.ScrolledWindow(child=self._sidebar_list, vexpand=True))
        toolbar.add_bottom_bar(self._build_status_bar())

        return Adw.NavigationPage(title="Clicky Clicker", child=toolbar)

    def _build_status_bar(self) -> Gtk.Widget:
        """A persistent indicator, so a running macro is always visible."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(12)
        box.set_margin_end(6)

        self._status_icon = Gtk.Image(icon_name="media-playback-pause-symbolic")
        box.append(self._status_icon)

        self._status_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        # Capped to two lines with ellipsis, not left to grow freely: a long
        # message in the narrow sidebar (min width 220px) can wrap to three or
        # more lines, and this area does not scroll, so an uncapped label can
        # be pushed past the bottom of a short window. The full text is always
        # available as a tooltip.
        self._status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._status_label.set_lines(2)
        self._status_label.add_css_class("caption")
        self._status_label.add_css_class("dim-label")
        box.append(self._status_label)

        self._status_stop = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self._status_stop.add_css_class("flat")
        self._status_stop.add_css_class("destructive-action")
        self._status_stop.set_tooltip_text("Stop every running macro")
        self._status_stop.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Stop every running macro"]
        )
        self._status_stop.set_visible(False)
        self._status_stop.connect("clicked", lambda *_: self.stop_all())
        box.append(self._status_stop)

        return box

    def _install_actions(self) -> None:
        entries: list[tuple[str, Callable[[], None], list[str]]] = [
            # Deliberately not bound to plain Escape: that is installed as a
            # window-wide accelerator, which GTK's shortcut controller matches
            # before a modal dialog's own key handling ever sees the event --
            # so Escape inside any dialog (the key chooser, cancelling a
            # rename, ...) would silently stop every macro instead of closing
            # the dialog. Ctrl+Alt+Esc, handled independently by the daemon,
            # remains the true system-wide emergency stop.
            ("stop-all", self.stop_all, ["<Control>period"]),
            ("new-macro", self._on_new_macro, ["<Control>n"]),
            ("shortcuts", self._show_shortcuts, ["<Control>question"]),
        ]
        application = self.get_application()
        for name, handler, accelerators in entries:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, run=handler: run())
            self.add_action(action)
            if application is not None:
                application.set_accels_for_action(f"win.{name}", accelerators)

    # --- Navigation -----------------------------------------------------

    def _on_section_selected(self, _box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        name = row.get_name()
        self._content_stack.set_visible_child_name(name)
        if name == "status":
            self._status_page.refresh()

    def open_macro(self, macro: Macro) -> None:
        """Push the editor for *macro*."""
        if self._editor is not None:
            self._editor.flush()

        self._editor = MacroEditorPage(
            self._library,
            macro,
            on_test=self.test_macro,
            on_stop=self.stop_all,
            on_record=self._on_record,
            on_toast=self.toast,
        )
        self._macros_navigation.push(self._editor)

    def _on_editor_popped(self, _view: Adw.NavigationView, page: Adw.NavigationPage) -> None:
        if page is self._editor:
            self._editor.flush()
            self._editor = None

    def _on_new_macro(self) -> None:
        self._sidebar_list.select_row(self._sidebar_list.get_row_at_index(0))
        self._macros_page.create_macro()

    def _on_record(self, macro: Macro) -> None:
        def finished(actions: list) -> None:
            if self._editor is not None and self._editor.macro is macro:
                self._editor.append_actions(actions)
                self.toast(f"Added {len(actions)} recorded action(s)")

        record_macro(self, self._library.settings, finished)

    # --- Macro control --------------------------------------------------

    def test_macro(self, macro: Macro) -> None:
        """Run a macro once, through the daemon.

        Testing goes through the same path a real trigger uses, so what is
        tested is genuinely what will happen.
        """
        if not macro.actions:
            self.toast("This macro has no actions yet")
            return

        if self._library.daemon.run_macro(macro.id, once=True):
            self.toast(f"Running “{macro.name}” once")
            self._poll_status()
            return

        status = self._library.daemon.status()
        if not status.connected:
            self.toast("The background service is not running — see the Status page")
        else:
            self.toast(f"Could not run “{macro.name}”")

    def stop_all(self) -> None:
        """Stop every running macro."""
        if self._library.daemon.stop_all():
            self.toast("Stopped all macros")
        self._poll_status()

    # --- Status polling -------------------------------------------------

    def _start_polling(self) -> None:
        if self._poll_source is None:
            self._poll_source = GLib.timeout_add(_POLL_INTERVAL_MS, self._poll_status)
        self._poll_status()

    def _stop_polling(self) -> None:
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
            self._poll_source = None

    def _poll_status(self) -> bool:
        status = self._library.daemon.status()
        self._render_status(status)
        return GLib.SOURCE_CONTINUE

    def _render_status(self, status: DaemonStatus) -> None:
        summary = status.summary()
        self._status_label.set_label(summary)
        self._status_label.set_tooltip_text(summary)

        running = bool(status.running)
        self._status_stop.set_visible(running)
        if running:
            indicator = "media-playback-start-symbolic"
        elif status.connected and status.enabled:
            indicator = "emblem-ok-symbolic"
        else:
            indicator = "dialog-warning-symbolic"
        self._status_icon.set_from_icon_name(indicator)

        running_ids = {entry.get("id", "") for entry in status.running}
        self._macros_page.set_running(running_ids)
        if self._editor is not None:
            self._editor.set_running(self._editor.macro.id in running_ids)

    # --- Miscellaneous --------------------------------------------------

    def toast(self, message: str, undo: Callable[[], None] | None = None) -> None:
        """Show a transient message, optionally with an Undo button."""
        toast = plain(Adw.Toast(timeout=4), title=message)
        if undo is not None:
            toast.set_button_label("Undo")
            toast.connect("button-clicked", lambda *_: undo())
        self._toasts.add_toast(toast)

    def _report_load_problems(self) -> None:
        """Tell the user about files that could not be read, rather than hiding them."""
        problems = self._library.load_problems
        if not problems:
            return
        dialog = Adw.AlertDialog(
            heading="Some Files Could Not Be Read",
            body=(
                "These files were skipped. The rest of your library loaded normally.\n\n"
                + "\n".join(problems[:10])
            ),
        )
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def _report_screen_size(self) -> None:
        """Tell the daemon how big the desktop is, for absolute pointer moves."""
        width, height = desktop_size(self.get_display())
        self._library.daemon.set_screen_size(width, height)

    def show_preferences(self) -> None:
        """Open the preferences dialog."""
        PreferencesDialog(self._library, self._gsettings, on_toast=self.toast).present(self)

    def _show_shortcuts(self) -> None:
        _shortcuts_window(self).present()

    # --- Geometry -------------------------------------------------------

    def _restore_geometry(self) -> None:
        if self._gsettings is None:
            return
        width = self._gsettings.get_int("window-width")
        height = self._gsettings.get_int("window-height")
        if width > 0 and height > 0:
            self.set_default_size(width, height)
        if self._gsettings.get_boolean("window-maximized"):
            self.maximize()

    def _save_geometry(self) -> None:
        if self._gsettings is None:
            return
        if not self.is_maximized():
            width, height = self.get_default_size()
            self._gsettings.set_int("window-width", width)
            self._gsettings.set_int("window-height", height)
        self._gsettings.set_boolean("window-maximized", self.is_maximized())

    def _on_close(self, *_args: object) -> bool:
        if self._editor is not None:
            self._editor.flush()
        self._save_geometry()
        self._stop_polling()
        return False


def _shortcuts_window(parent: Gtk.Window) -> Gtk.ShortcutsWindow:
    """Build the standard keyboard shortcuts window."""
    section = Gtk.ShortcutsSection(section_name="shortcuts", max_height=10, visible=True)

    general = Gtk.ShortcutsGroup(title="General", visible=True)
    for accelerator, title in (
        ("<Control>n", "New macro"),
        ("<Control>comma", "Preferences"),
        ("<Control>q", "Quit"),
        ("<Control>question", "Keyboard shortcuts"),
    ):
        general.add_shortcut(
            Gtk.ShortcutsShortcut(accelerator=accelerator, title=title, visible=True)
        )
    section.add_group(general)

    macros = Gtk.ShortcutsGroup(title="Macros", visible=True)
    for accelerator, title in (("<Control>period", "Stop all running macros"),):
        macros.add_shortcut(
            Gtk.ShortcutsShortcut(accelerator=accelerator, title=title, visible=True)
        )
    section.add_group(macros)

    window = Gtk.ShortcutsWindow(modal=True, transient_for=parent)
    window.add_section(section)
    return window
