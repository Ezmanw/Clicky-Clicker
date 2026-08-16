"""The macro library: create, organise, import and export presets.

A preset and a saved macro are the same object here -- there is no separate
"preset" concept to keep in sync -- so exporting is writing one file out and
importing is reading one in.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from ...models import Macro
from ...services import MacroLibrary
from ..widgets.rows import plain

__all__ = ["MacrosPage"]


class MacrosPage(Adw.NavigationPage):
    """Lists every saved macro."""

    def __init__(
        self,
        library: MacroLibrary,
        *,
        on_open: Callable[[Macro], None],
        on_test: Callable[[Macro], None],
        on_stop: Callable[[], None],
        on_toast: Callable[..., None],
    ) -> None:
        super().__init__()
        self._library = library
        self._on_open = on_open
        self._on_test = on_test
        self._on_stop = on_stop
        self._on_toast = on_toast
        self._running_ids: set[str] = set()

        self.set_title("Macros")
        self.set_tag("macros")

        self._group = Adw.PreferencesGroup()
        self._rows: list[Adw.ActionRow] = []
        self._build()

        library.connect_macros_changed(self.refresh)
        library.connect_bindings_changed(self.refresh)
        self.refresh()

    # --- Construction ---------------------------------------------------

    def _build(self) -> None:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Macros"))

        new_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="list-add-symbolic", label="New Macro")
        )
        new_button.add_css_class("suggested-action")
        new_button.connect("clicked", lambda *_: self.create_macro())
        header.pack_start(new_button)

        menu = Gio.Menu()
        menu.append("Import Preset…", "macros.import")
        menu.append("Stop All Macros", "macros.stop-all")
        menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu)
        menu_button.set_tooltip_text("Library options")
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Library options"])
        header.pack_end(menu_button)

        actions = Gio.SimpleActionGroup()
        import_action = Gio.SimpleAction.new("import", None)
        import_action.connect("activate", lambda *_: self._on_import())
        actions.add_action(import_action)
        stop_action = Gio.SimpleAction.new("stop-all", None)
        stop_action.connect("activate", lambda *_: self._on_stop())
        actions.add_action(stop_action)
        self.insert_action_group("macros", actions)

        self._empty = Adw.StatusPage(
            icon_name="folder-symbolic",
            title="No Macros Yet",
            description="Create a macro to get started, or import a preset someone shared.",
        )
        create = Gtk.Button(label="Create a Macro")
        create.add_css_class("suggested-action")
        create.add_css_class("pill")
        create.set_halign(Gtk.Align.CENTER)
        create.connect("clicked", lambda *_: self.create_macro())
        self._empty.set_child(create)

        page = Adw.PreferencesPage()
        page.add(self._group)

        self._stack = Gtk.Stack()
        self._stack.add_named(page, "list")
        self._stack.add_named(self._empty, "empty")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    # --- Rendering ------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the list from the library."""
        for row in self._rows:
            self._group.remove(row)
        self._rows = []

        macros = self._library.macros
        self._stack.set_visible_child_name("list" if macros else "empty")
        self._group.set_title(f"{len(macros)} Macro(s)" if macros else "")

        for macro in macros:
            self._group.add(self._build_row(macro))

    def _build_row(self, macro: Macro) -> Adw.ActionRow:
        row = plain(Adw.ActionRow(), title=macro.name)
        row.set_subtitle(self._describe(macro))
        row.set_activatable(True)
        row.connect("activated", lambda *_: self._on_open(macro))

        icon = Gtk.Image(icon_name="input-keyboard-symbolic")
        row.add_prefix(icon)

        if macro.id in self._running_ids:
            spinner = Gtk.Spinner(spinning=True)
            spinner.set_tooltip_text("This macro is running")
            row.add_suffix(spinner)

            stop = Gtk.Button(icon_name="media-playback-stop-symbolic")
            stop.add_css_class("flat")
            stop.add_css_class("destructive-action")
            stop.set_valign(Gtk.Align.CENTER)
            stop.set_tooltip_text("Stop this macro")
            stop.update_property([Gtk.AccessibleProperty.LABEL], [f"Stop {macro.name}"])
            stop.connect("clicked", lambda *_: self._on_stop())
            row.add_suffix(stop)
        else:
            test = Gtk.Button(icon_name="media-playback-start-symbolic")
            test.add_css_class("flat")
            test.set_valign(Gtk.Align.CENTER)
            test.set_tooltip_text("Run this macro once")
            test.update_property([Gtk.AccessibleProperty.LABEL], [f"Test {macro.name}"])
            test.connect("clicked", lambda *_: self._on_test(macro))
            row.add_suffix(test)

        row.add_suffix(self._build_row_menu(macro))
        self._rows.append(row)
        return row

    def _build_row_menu(self, macro: Macro) -> Gtk.Widget:
        button = Gtk.MenuButton(icon_name="view-more-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("Macro options")
        button.update_property([Gtk.AccessibleProperty.LABEL], [f"Options for {macro.name}"])

        entries = (
            ("Edit", lambda: self._on_open(macro)),
            ("Rename…", lambda: self._on_rename(macro)),
            ("Duplicate", lambda: self._on_duplicate(macro)),
            ("Export…", lambda: self._on_export(macro)),
            ("Delete…", lambda: self._on_delete(macro)),
        )

        popover = Gtk.Popover()
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("navigation-sidebar")
        for label, _handler in entries:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=label, xalign=0.0, margin_top=6, margin_bottom=6))
            listbox.append(row)
        listbox.connect(
            "row-activated",
            lambda _box, row: (popover.popdown(), entries[row.get_index()][1]()),
        )
        popover.set_child(listbox)
        button.set_popover(popover)
        return button

    def _describe(self, macro: Macro) -> str:
        """Subtitle summarising size, timing and assignment."""
        steps = sum(1 for action in macro.actions if action.enabled)
        parts = [f"{steps} step(s)"]

        duration = macro.total_duration_ms()
        if duration:
            parts.append(f"~{duration} ms per pass")

        bindings = self._library.bindings_for(macro.id)
        if bindings:
            parts.append(", ".join(binding.input_label() for binding in bindings))
        else:
            parts.append("Not assigned")

        if macro.description:
            parts.append(macro.description)
        return " · ".join(parts)

    def set_running(self, macro_ids: set[str]) -> None:
        """Show which macros are currently playing."""
        if macro_ids != self._running_ids:
            self._running_ids = set(macro_ids)
            self.refresh()

    # --- Commands -------------------------------------------------------

    def create_macro(self) -> None:
        """Create a new macro and open it. Also reachable from the window's action."""
        try:
            macro = self._library.create_macro()
        except OSError as exc:
            self._on_toast(f"Could not create the macro: {exc}")
            return
        self._on_open(macro)

    def _on_rename(self, macro: Macro) -> None:
        dialog = Adw.AlertDialog(heading="Rename Macro", body="Choose a new name.")
        entry = Adw.EntryRow(title="Name")
        entry.set_text(macro.name)
        group = Adw.PreferencesGroup()
        group.add(entry)
        dialog.set_extra_child(group)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def responded(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "rename":
                return
            try:
                self._library.rename_macro(macro, entry.get_text())
            except OSError as exc:
                self._on_toast(f"Could not rename the macro: {exc}")

        dialog.connect("response", responded)
        dialog.present(self)

    def _on_duplicate(self, macro: Macro) -> None:
        try:
            copy = self._library.duplicate_macro(macro)
        except OSError as exc:
            self._on_toast(f"Could not duplicate the macro: {exc}")
            return
        self._on_toast(f"Created “{copy.name}”")

    def _on_delete(self, macro: Macro) -> None:
        bindings = self._library.bindings_for(macro.id)
        body = f"“{macro.name}” will be permanently deleted."
        if bindings:
            inputs = ", ".join(binding.input_label() for binding in bindings)
            body += f"\n\n{inputs} will be left without a macro until you reassign them."

        dialog = Adw.AlertDialog(heading="Delete Macro?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def responded(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "delete":
                self._library.delete_macro(macro)
                self._on_toast(f"Deleted “{macro.name}”")

        dialog.connect("response", responded)
        dialog.present(self)

    # --- Import and export ----------------------------------------------

    def _preset_filter(self) -> Gtk.FileFilter:
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Clicky Clicker Presets")
        file_filter.add_pattern("*.json")
        file_filter.add_mime_type("application/json")
        return file_filter

    def _on_import(self) -> None:
        dialog = Gtk.FileDialog(title="Import Preset")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(self._preset_filter())
        dialog.set_filters(filters)

        def finished(source: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                selected = source.open_finish(result)
            except GLib.Error:
                return  # The user cancelled.
            if selected is None or selected.get_path() is None:
                return
            try:
                macro = self._library.import_macro(Path(selected.get_path()))
            except (ValueError, OSError) as exc:
                self._show_error("Could Not Import Preset", str(exc))
                return
            self._on_toast(f"Imported “{macro.name}”")

        dialog.open(self._window(), None, finished)

    def _on_export(self, macro: Macro) -> None:
        dialog = Gtk.FileDialog(title="Export Preset")
        dialog.set_initial_name(f"{_safe_filename(macro.name)}.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(self._preset_filter())
        dialog.set_filters(filters)

        def finished(source: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                selected = source.save_finish(result)
            except GLib.Error:
                return  # The user cancelled.
            if selected is None or selected.get_path() is None:
                return
            try:
                self._library.export_macro(macro, Path(selected.get_path()))
            except OSError as exc:
                self._show_error("Could Not Export Preset", str(exc))
                return
            self._on_toast(f"Exported “{macro.name}”")

        dialog.save(self._window(), None, finished)

    # --- Helpers --------------------------------------------------------

    def _window(self) -> Gtk.Window | None:
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _show_error(self, heading: str, detail: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=detail)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)


def _safe_filename(name: str) -> str:
    """Turn a macro name into something safe to suggest as a filename."""
    cleaned = "".join(
        character if character.isalnum() or character in " -_" else "-"
        for character in name
    )
    return cleaned.strip().replace(" ", "-").lower() or "preset"
