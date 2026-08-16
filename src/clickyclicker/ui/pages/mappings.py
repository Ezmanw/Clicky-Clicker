"""Input mappings: which physical key or button does what.

Kept separate from the macro library because the relationship is many-to-one --
one macro can be triggered by several inputs -- and because remapping a key to
another key involves no macro at all.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk

from ...models import Binding, BindingKind
from ...services import MacroLibrary
from ..dialogs.binding_editor import edit_binding
from ..widgets.rows import plain

__all__ = ["MappingsPage"]


class MappingsPage(Adw.NavigationPage):
    """Lists and edits every input binding."""

    def __init__(self, library: MacroLibrary, *, on_toast: Callable[..., None]) -> None:
        super().__init__()
        self._library = library
        self._on_toast = on_toast

        self.set_title("Mappings")
        self.set_tag("mappings")

        self._macro_group = Adw.PreferencesGroup(
            title="Macro Triggers", description="Inputs that run a macro."
        )
        self._remap_group = Adw.PreferencesGroup(
            title="Remapped Inputs", description="Inputs replaced by a different key."
        )
        self._problem_banner = plain(Adw.Banner())
        self._problem_banner.set_revealed(False)
        self._rows: list[tuple[Adw.PreferencesGroup, Adw.ActionRow]] = []

        self._build()
        library.connect_bindings_changed(self.refresh)
        library.connect_macros_changed(self.refresh)
        self.refresh()

    # --- Construction ---------------------------------------------------

    def _build(self) -> None:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Mappings"))

        add = Gtk.Button(
            child=Adw.ButtonContent(icon_name="list-add-symbolic", label="New Mapping")
        )
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda *_: self.create_binding())
        header.pack_start(add)

        self._empty = Adw.StatusPage(
            icon_name="input-mouse-symbolic",
            title="No Mappings Yet",
            description=(
                "Assign a key or mouse button to a macro, or remap one input to "
                "another, such as Caps Lock to Escape."
            ),
        )
        create = Gtk.Button(label="Create a Mapping")
        create.add_css_class("suggested-action")
        create.add_css_class("pill")
        create.set_halign(Gtk.Align.CENTER)
        create.connect("clicked", lambda *_: self.create_binding())
        self._empty.set_child(create)

        page = Adw.PreferencesPage()
        page.add(self._macro_group)
        page.add(self._remap_group)

        self._stack = Gtk.Stack()
        self._stack.add_named(page, "list")
        self._stack.add_named(self._empty, "empty")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self._problem_banner)
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    # --- Rendering ------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the lists from the library."""
        for group, row in self._rows:
            group.remove(row)
        self._rows = []

        bindings = self._library.bindings
        self._stack.set_visible_child_name("list" if bindings else "empty")

        macro_bindings = [b for b in bindings if b.kind is BindingKind.RUN_MACRO]
        other_bindings = [b for b in bindings if b.kind is not BindingKind.RUN_MACRO]

        for binding in macro_bindings:
            self._add_row(self._macro_group, binding)
        for binding in other_bindings:
            self._add_row(self._remap_group, binding)

        self._macro_group.set_visible(bool(macro_bindings))
        self._remap_group.set_visible(bool(other_bindings))
        self._render_problems()

    def _add_row(self, group: Adw.PreferencesGroup, binding: Binding) -> None:
        broken = binding in self._library.broken_bindings()
        macro_name = self._library.macro_name(binding.macro_id)

        row = plain(Adw.ActionRow(), title=binding.input_label())
        row.set_subtitle(
            "The macro this was assigned to no longer exists"
            if broken
            else binding.describe(macro_name)
        )
        row.set_activatable(True)
        row.connect("activated", lambda *_: self._on_edit(binding))

        icon = Gtk.Image(
            icon_name=(
                "input-mouse-symbolic"
                if binding.input.startswith("BTN_")
                else "input-keyboard-symbolic"
            )
        )
        row.add_prefix(icon)

        if broken:
            warning = Gtk.Image(icon_name="dialog-warning-symbolic")
            warning.set_tooltip_text("This mapping is incomplete and will not be applied")
            row.add_suffix(warning)

        toggle = Gtk.Switch(active=binding.enabled, valign=Gtk.Align.CENTER)
        toggle.set_tooltip_text("Apply this mapping")
        toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"Enable the {binding.input_label()} mapping"]
        )
        toggle.connect("notify::active", self._on_toggled, binding)
        row.add_suffix(toggle)

        row.add_suffix(self._build_row_menu(binding))
        group.add(row)
        self._rows.append((group, row))

    def _build_row_menu(self, binding: Binding) -> Gtk.Widget:
        button = Gtk.MenuButton(icon_name="view-more-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("Mapping options")
        button.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"Options for the {binding.input_label()} mapping"]
        )

        entries = (
            ("Edit…", lambda: self._on_edit(binding)),
            ("Duplicate", lambda: self._on_duplicate(binding)),
            ("Delete", lambda: self._on_delete(binding)),
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

    def _render_problems(self) -> None:
        """Surface duplicate and broken bindings rather than silently ignoring them."""
        messages: list[str] = []

        conflicts = self._library.binding_set.conflicts()
        if conflicts:
            names = ", ".join(conflicts)
            messages.append(f"More than one mapping uses {names}.")

        broken = self._library.broken_bindings()
        if broken:
            inputs = ", ".join(binding.input_label() for binding in broken)
            messages.append(f"{inputs} is incomplete and will not be applied.")

        if messages:
            self._problem_banner.set_title(" ".join(messages))
            self._problem_banner.set_revealed(True)
        else:
            self._problem_banner.set_revealed(False)

    # --- Commands -------------------------------------------------------

    def create_binding(self) -> None:
        """Create a new mapping. Also reachable from the window's action."""
        macros = self._library.macros
        binding = Binding(
            input="KEY_F6",
            kind=BindingKind.RUN_MACRO if macros else BindingKind.REMAP,
            macro_id=macros[0].id if macros else None,
        )

        def save(created: Binding) -> None:
            try:
                self._library.add_binding(created)
            except OSError as exc:
                self._on_toast(f"Could not save the mapping: {exc}")
                return
            self._on_toast(f"{created.input_label()} mapped")

        edit_binding(self, self._library, binding, is_new=True, on_save=save)

    def _on_edit(self, binding: Binding) -> None:
        # Edit a copy so cancelling genuinely discards the changes.
        draft = binding.duplicate()
        draft.id = binding.id

        def save(edited: Binding) -> None:
            for index, existing in enumerate(self._library.binding_set.bindings):
                if existing.id == edited.id:
                    self._library.binding_set.bindings[index] = edited
                    break
            try:
                self._library.save_bindings()
            except OSError as exc:
                self._on_toast(f"Could not save the mapping: {exc}")

        edit_binding(self, self._library, draft, is_new=False, on_save=save)

    def _on_duplicate(self, binding: Binding) -> None:
        try:
            self._library.duplicate_binding(binding)
        except OSError as exc:
            self._on_toast(f"Could not duplicate the mapping: {exc}")

    def _on_delete(self, binding: Binding) -> None:
        label = binding.input_label()

        def restore() -> None:
            try:
                self._library.add_binding(binding)
            except OSError as exc:
                self._on_toast(f"Could not restore the mapping: {exc}")

        try:
            self._library.remove_binding(binding)
        except OSError as exc:
            self._on_toast(f"Could not delete the mapping: {exc}")
            return
        self._on_toast(f"Removed the {label} mapping", restore)

    def _on_toggled(self, switch: Gtk.Switch, _param: object, binding: Binding) -> None:
        if binding.enabled == switch.get_active():
            return
        binding.enabled = switch.get_active()
        try:
            self._library.save_bindings()
        except OSError as exc:
            self._on_toast(f"Could not save the mapping: {exc}")
