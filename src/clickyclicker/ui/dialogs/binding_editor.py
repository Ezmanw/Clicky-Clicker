"""Creating and editing an input binding.

A binding answers three questions, and the dialog asks them in that order:
which input, what should it do, and how should activating it behave.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk

from ...models import Binding, BindingKind, TriggerMode
from ...models.keys import format_combo, label_for
from ...models.macro import TRIGGER_LABELS
from ...services import MacroLibrary
from .combo_editor import edit_combo
from ..widgets.rows import plain
from .key_chooser import choose_key

__all__ = ["BindingEditorDialog", "edit_binding"]

_KIND_LABELS: list[tuple[BindingKind, str, str]] = [
    (BindingKind.RUN_MACRO, "Run a macro", "Play one of your saved macros"),
    (BindingKind.REMAP, "Act as another input", "Replace this input with a different key"),
    (BindingKind.DISABLE, "Do nothing", "Swallow this input entirely"),
]

#: Sentinel entry meaning "use whatever the macro itself specifies".
_INHERIT = "Use the macro's own setting"


class BindingEditorDialog(Adw.Dialog):
    """Modal editor for one :class:`~clickyclicker.models.binding.Binding`."""

    def __init__(
        self,
        library: MacroLibrary,
        binding: Binding,
        *,
        is_new: bool,
        on_save: Callable[[Binding], None],
    ) -> None:
        super().__init__()
        self._library = library
        self._binding = binding
        self._on_save = on_save
        self._updating = False

        title = "New Mapping" if is_new else "Edit Mapping"
        self.set_title(title)
        self.set_content_width(520)
        self.set_content_height(640)

        self._macros = library.macros
        self._build(title)
        self._render()

    # --- Construction ---------------------------------------------------

    def _build(self, title: str) -> None:
        page = Adw.PreferencesPage()

        trigger_group = Adw.PreferencesGroup(
            title="Input",
            description="The key or mouse button that activates this mapping.",
        )
        self._input_row = plain(Adw.ActionRow(title="Trigger"))
        self._input_row.add_prefix(Gtk.Image(icon_name="input-keyboard-symbolic"))
        change = Gtk.Button(label="Change")
        change.set_valign(Gtk.Align.CENTER)
        change.connect("clicked", lambda *_: self._choose_input())
        self._input_row.add_suffix(change)
        self._input_row.set_activatable_widget(change)
        trigger_group.add(self._input_row)
        page.add(trigger_group)

        action_group = Adw.PreferencesGroup(title="Action", description="What this input does.")
        kind_model = Gtk.StringList()
        for _kind, label, _detail in _KIND_LABELS:
            kind_model.append(label)
        self._kind_row = plain(Adw.ComboRow(title="Behaviour", model=kind_model))
        self._kind_row.connect("notify::selected", self._on_kind_changed)
        action_group.add(self._kind_row)

        self._macro_row = plain(Adw.ComboRow(title="Macro"))
        self._macro_row.set_model(self._macro_model())
        self._macro_row.connect("notify::selected", self._on_macro_changed)
        action_group.add(self._macro_row)

        self._output_row = plain(Adw.ActionRow(title="Acts As"))
        output_button = Gtk.Button(label="Choose…")
        output_button.set_valign(Gtk.Align.CENTER)
        output_button.connect("clicked", lambda *_: self._choose_output())
        self._output_row.add_suffix(output_button)
        self._output_row.set_activatable_widget(output_button)
        action_group.add(self._output_row)
        page.add(action_group)

        behaviour_group = Adw.PreferencesGroup(
            title="Activation",
            description="How pressing the input starts and stops the macro.",
        )
        self._trigger_options: list[TriggerMode | None] = [None, *TriggerMode]
        trigger_model = Gtk.StringList()
        trigger_model.append(_INHERIT)
        for mode in TriggerMode:
            trigger_model.append(TRIGGER_LABELS[mode])
        self._trigger_row = Adw.ComboRow(title="Trigger Behaviour", model=trigger_model)
        self._trigger_row.set_subtitle(
            "Override the macro's setting for this input only"
        )
        self._trigger_row.connect("notify::selected", self._on_trigger_changed)
        behaviour_group.add(self._trigger_row)

        self._suppress_row = Adw.SwitchRow(
            title="Hide the Original Input",
            subtitle=(
                "Stop the original key reaching other applications. Requires "
                "exclusive access to the device."
            ),
        )
        self._suppress_row.connect("notify::active", self._on_suppress_changed)
        behaviour_group.add(self._suppress_row)

        self._enabled_row = Adw.SwitchRow(
            title="Enabled", subtitle="Turn off to keep this mapping without applying it"
        )
        self._enabled_row.connect("notify::active", self._on_enabled_changed)
        behaviour_group.add(self._enabled_row)
        page.add(behaviour_group)

        summary_group = Adw.PreferencesGroup(title="Summary")
        self._summary_row = plain(Adw.ActionRow())
        self._summary_row.set_title_lines(0)
        self._summary_row.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
        summary_group.add(self._summary_row)
        page.add(summary_group)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)

        self._save_button = Gtk.Button(label="Save")
        self._save_button.add_css_class("suggested-action")
        self._save_button.connect("clicked", lambda *_: self._save())
        header.pack_end(self._save_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

    def _macro_model(self) -> Gtk.StringList:
        model = Gtk.StringList()
        if not self._macros:
            model.append("No macros available")
        for macro in self._macros:
            model.append(macro.name)
        return model

    # --- Rendering ------------------------------------------------------

    def _render(self) -> None:
        self._updating = True
        try:
            binding = self._binding

            self._input_row.set_subtitle(label_for(binding.input))
            self._kind_row.set_selected(
                next(i for i, (kind, _l, _d) in enumerate(_KIND_LABELS) if kind is binding.kind)
            )
            self._kind_row.set_subtitle(
                next(detail for kind, _l, detail in _KIND_LABELS if kind is binding.kind)
            )

            is_macro = binding.kind is BindingKind.RUN_MACRO
            is_remap = binding.kind is BindingKind.REMAP
            self._macro_row.set_visible(is_macro)
            self._output_row.set_visible(is_remap)
            self._trigger_row.set_visible(is_macro)

            if is_macro and self._macros:
                index = next(
                    (i for i, m in enumerate(self._macros) if m.id == binding.macro_id), 0
                )
                self._macro_row.set_selected(index)
                self._macro_row.set_subtitle("")
            elif is_macro:
                self._macro_row.set_subtitle("Create a macro first")
                self._macro_row.set_sensitive(False)

            self._output_row.set_subtitle(
                format_combo(binding.output) if binding.output else "Not set"
            )

            override_index = self._trigger_options.index(binding.trigger_override)
            self._trigger_row.set_selected(override_index)

            # Remaps and disables have no meaningful "also deliver the original".
            self._suppress_row.set_sensitive(is_macro)
            self._suppress_row.set_active(binding.effective_suppress())
            self._enabled_row.set_active(binding.enabled)

            self._summary_row.set_title(self._summary())
            self._save_button.set_sensitive(self._is_complete())
        finally:
            self._updating = False

    def _summary(self) -> str:
        binding = self._binding
        input_label = label_for(binding.input)

        if binding.kind is BindingKind.DISABLE:
            return f"{input_label} will do nothing at all."
        if binding.kind is BindingKind.REMAP:
            if not binding.output:
                return "Choose what this input should act as."
            return f"{input_label} will behave as {format_combo(binding.output)}."

        macro = self._library.macro(binding.macro_id)
        if macro is None:
            return "Choose a macro for this input to run."

        if binding.trigger_override is not None:
            behaviour = TRIGGER_LABELS[binding.trigger_override].lower()
            return f"{input_label} runs “{macro.name}” — {behaviour}."
        return macro.describe_behaviour(input_label)

    def _is_complete(self) -> bool:
        binding = self._binding
        if binding.kind is BindingKind.RUN_MACRO:
            return bool(binding.macro_id)
        if binding.kind is BindingKind.REMAP:
            return bool(binding.output)
        return True

    # --- Editing --------------------------------------------------------

    def _choose_input(self) -> None:
        def chosen(name: str) -> None:
            self._binding.input = name
            self._render()

        choose_key(
            self, title="Choose Trigger Input", selected=self._binding.input, on_chosen=chosen
        )

    def _choose_output(self) -> None:
        def changed(codes: list[str]) -> None:
            self._binding.output = list(codes)
            self._render()

        edit_combo(self, list(self._binding.output), changed)

    def _on_kind_changed(self, combo: Adw.ComboRow, _param: object) -> None:
        if self._updating:
            return
        self._binding.kind = _KIND_LABELS[combo.get_selected()][0]
        if self._binding.kind is BindingKind.RUN_MACRO and not self._binding.macro_id:
            if self._macros:
                self._binding.macro_id = self._macros[0].id
        self._render()

    def _on_macro_changed(self, combo: Adw.ComboRow, _param: object) -> None:
        if self._updating or not self._macros:
            return
        index = combo.get_selected()
        if 0 <= index < len(self._macros):
            self._binding.macro_id = self._macros[index].id
        self._render()

    def _on_trigger_changed(self, combo: Adw.ComboRow, _param: object) -> None:
        if self._updating:
            return
        self._binding.trigger_override = self._trigger_options[combo.get_selected()]
        self._render()

    def _on_suppress_changed(self, switch: Adw.SwitchRow, _param: object) -> None:
        if self._updating:
            return
        self._binding.suppress_original = switch.get_active()

    def _on_enabled_changed(self, switch: Adw.SwitchRow, _param: object) -> None:
        if self._updating:
            return
        self._binding.enabled = switch.get_active()

    def _save(self) -> None:
        if not self._is_complete():
            return
        self._on_save(self._binding)
        self.close()


def edit_binding(
    parent: Gtk.Widget,
    library: MacroLibrary,
    binding: Binding,
    *,
    is_new: bool,
    on_save: Callable[[Binding], None],
) -> BindingEditorDialog:
    """Open the binding editor anchored to *parent*."""
    dialog = BindingEditorDialog(library, binding, is_new=is_new, on_save=on_save)
    dialog.present(parent)
    return dialog
