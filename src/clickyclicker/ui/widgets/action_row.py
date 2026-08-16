"""One step of a macro, as an editable row.

Every control is built from the action's :class:`~clickyclicker.models.action.ActionSpec`
rather than from per-type UI code, so adding a new action type to the model
makes it editable here automatically.

The row is an ``AdwExpanderRow``: collapsed it reads as a sentence -- ``Tap E
for 1 ms`` -- so a macro can be understood at a glance, and expanded it exposes
that step's parameters.  Reordering is available both by drag and by explicit
Move Up / Move Down commands, because drag alone is not reachable from the
keyboard.
"""

from __future__ import annotations

from typing import Any, Callable

from gi.repository import Adw, Gdk, GObject, Gtk

from ...models import keys
from ...models.action import (
    ACTION_SPECS,
    ActionType,
    MacroAction,
    ParamKind,
    ParamSpec,
    action_types_in_order,
)
from ..dialogs.combo_editor import edit_combo
from ..dialogs.key_chooser import choose_key
from ..dialogs.position_picker import pick_position
from .rows import icon_button, plain

__all__ = ["ActionEditorRow"]

#: Action types whose coordinates can be filled in by pointing at the screen.
_POSITIONAL = {ActionType.MOUSE_MOVE, ActionType.MOUSE_CLICK_AT}


class ActionEditorRow(Adw.ExpanderRow):
    """An editable macro action."""

    __gtype_name__ = "ClickyActionEditorRow"

    def __init__(
        self,
        action: MacroAction,
        index: int,
        *,
        total: int,
        on_changed: Callable[[], None],
        on_command: Callable[[str, int], None],
    ) -> None:
        """
        :param on_changed: called after any parameter edit, so the editor can
            re-render summaries and mark the macro dirty.
        :param on_command: called with a command name (``delete``, ``duplicate``,
            ``move_up``, ``move_down``, ``insert_above``, ``insert_below``) and
            this row's index.
        """
        super().__init__()
        plain(self)
        self._action = action
        self._index = index
        self._total = total
        self._on_changed = on_changed
        self._on_command = on_command
        self._param_rows: list[Gtk.Widget] = []
        self._updating = False

        self._icon = Gtk.Image(icon_name=action.spec.icon_name)
        self.add_prefix(self._icon)

        self._disabled_badge = Gtk.Label(label="Disabled")
        self._disabled_badge.add_css_class("dim-label")
        self._disabled_badge.add_css_class("caption")
        self._disabled_badge.set_valign(Gtk.Align.CENTER)
        self._disabled_badge.set_visible(not action.enabled)
        self.add_suffix(self._disabled_badge)

        self.add_suffix(self._build_reorder_controls())
        self.add_suffix(self._build_menu_button())

        self._build_parameters()
        self.refresh_titles()
        self._install_drag_and_drop()

    # --- Public ---------------------------------------------------------

    @property
    def action(self) -> MacroAction:
        return self._action

    @property
    def index(self) -> int:
        return self._index

    # --- Header controls ------------------------------------------------

    def _build_reorder_controls(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.set_valign(Gtk.Align.CENTER)
        box.add_css_class("linked")

        self._up = icon_button("go-up-symbolic", "Move this action up")
        self._up.set_sensitive(self._index > 0)
        self._up.connect("clicked", lambda *_: self._on_command("move_up", self._index))
        box.append(self._up)

        self._down = icon_button("go-down-symbolic", "Move this action down")
        self._down.set_sensitive(self._index < self._total - 1)
        self._down.connect("clicked", lambda *_: self._on_command("move_down", self._index))
        box.append(self._down)

        return box

    def _build_menu_button(self) -> Gtk.Widget:
        menu = [
            ("Insert Action Above", "insert_above"),
            ("Insert Action Below", "insert_below"),
            ("Duplicate", "duplicate"),
            ("Delete", "delete"),
        ]

        button = Gtk.MenuButton(icon_name="view-more-symbolic")
        button.set_tooltip_text("More actions")
        button.update_property([Gtk.AccessibleProperty.LABEL], ["More actions for this step"])
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)

        popover = Gtk.Popover()
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("navigation-sidebar")
        for label, command in menu:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=label, xalign=0.0, margin_top=6, margin_bottom=6))
            row.set_activatable(True)
            listbox.append(row)
        listbox.connect(
            "row-activated",
            lambda _box, row: (
                popover.popdown(),
                self._on_command(menu[row.get_index()][1], self._index),
            ),
        )
        popover.set_child(listbox)
        button.set_popover(popover)
        return button

    # --- Parameter editors ----------------------------------------------

    def _build_parameters(self) -> None:
        """Rebuild the expanded content for the current action type."""
        for row in self._param_rows:
            self.remove(row)
        self._param_rows = []

        self._add_row(self._build_type_row())
        for spec in self._action.spec.params:
            row = self._build_param_row(spec)
            if row is not None:
                self._add_row(row)

        if self._action.type in _POSITIONAL:
            self._add_row(self._build_pick_position_row())

        self._add_row(self._build_enabled_row())

    def _add_row(self, row: Gtk.Widget) -> None:
        self.add_row(row)
        self._param_rows.append(row)

    def _build_type_row(self) -> Adw.ComboRow:
        """Lets the step be changed to a different kind of action in place."""
        types = action_types_in_order()
        model = Gtk.StringList()
        for action_type in types:
            model.append(ACTION_SPECS[action_type].label)

        row = Adw.ComboRow(title="Action", model=model)
        row.set_subtitle("What this step does")
        row.set_selected(types.index(self._action.type))

        def on_selected(combo: Adw.ComboRow, _param: object) -> None:
            if self._updating:
                return
            chosen = types[combo.get_selected()]
            if chosen is self._action.type:
                return
            # Parameters are type-specific, so reset them to the new type's
            # defaults rather than carrying meaningless values across.
            self._action.type = chosen
            self._action.params = ACTION_SPECS[chosen].defaults()
            self._icon.set_from_icon_name(ACTION_SPECS[chosen].icon_name)
            self._build_parameters()
            self.refresh_titles()
            self._on_changed()

        row.connect("notify::selected", on_selected)
        return row

    def _build_param_row(self, spec: ParamSpec) -> Gtk.Widget | None:
        if spec.kind is ParamKind.KEY:
            return self._build_key_row(spec)
        if spec.kind is ParamKind.KEY_LIST:
            return self._build_key_list_row(spec)
        if spec.kind in (ParamKind.DURATION, ParamKind.COORDINATE, ParamKind.INTEGER):
            return self._build_number_row(spec)
        if spec.kind is ParamKind.BOOLEAN:
            return self._build_boolean_row(spec)
        if spec.kind is ParamKind.CHOICE:
            return self._build_choice_row(spec)
        return None

    def _build_key_row(self, spec: ParamSpec) -> Adw.ActionRow:
        current = str(self._action.params.get(spec.key) or "")
        mouse_only = spec.key == "button"

        row = plain(Adw.ActionRow(title=spec.label))
        row.set_subtitle(keys.label_for(current) if current else "Not set")

        button = Gtk.Button(label="Change")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(f"Choose the {spec.label.lower()}")

        def on_clicked(_button: Gtk.Button) -> None:
            def chosen(name: str) -> None:
                self._action.params[spec.key] = name
                row.set_subtitle(keys.label_for(name))
                self.refresh_titles()
                self._on_changed()

            choose_key(
                self,
                title=f"Choose {spec.label}",
                selected=str(self._action.params.get(spec.key) or ""),
                keyboard=not mouse_only,
                mouse=True,
                on_chosen=chosen,
            )

        button.connect("clicked", on_clicked)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    def _build_key_list_row(self, spec: ParamSpec) -> Adw.ActionRow:
        current = [str(k) for k in (self._action.params.get(spec.key) or [])]

        row = plain(Adw.ActionRow(title=spec.label))
        row.set_subtitle(keys.format_combo(current))

        button = Gtk.Button(label="Edit")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("Edit this key combination")

        def on_clicked(_button: Gtk.Button) -> None:
            def changed(codes: list[str]) -> None:
                self._action.params[spec.key] = list(codes)
                row.set_subtitle(keys.format_combo(codes))
                self.refresh_titles()
                self._on_changed()

            edit_combo(self, [str(k) for k in (self._action.params.get(spec.key) or [])], changed)

        button.connect("clicked", on_clicked)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    def _build_number_row(self, spec: ParamSpec) -> Adw.SpinRow:
        adjustment = Gtk.Adjustment(
            value=_number(self._action.params.get(spec.key), spec.default),
            lower=spec.minimum,
            upper=spec.maximum,
            step_increment=spec.step or 1,
            page_increment=max(10, (spec.step or 1) * 10),
        )
        row = Adw.SpinRow(title=spec.label, adjustment=adjustment)
        if spec.unit:
            row.set_subtitle(f"In {spec.unit}")

        def on_value(spin: Adw.SpinRow, _param: object) -> None:
            if self._updating:
                return
            self._action.params[spec.key] = int(spin.get_value())
            self.refresh_titles()
            self._on_changed()

        row.connect("notify::value", on_value)
        return row

    def _build_boolean_row(self, spec: ParamSpec) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=spec.label)
        row.set_active(bool(self._action.params.get(spec.key, spec.default)))

        def on_active(switch: Adw.SwitchRow, _param: object) -> None:
            if self._updating:
                return
            self._action.params[spec.key] = switch.get_active()
            self.refresh_titles()
            self._on_changed()

        row.connect("notify::active", on_active)
        return row

    def _build_choice_row(self, spec: ParamSpec) -> Adw.ComboRow:
        values = [value for value, _label in spec.choices]
        model = Gtk.StringList()
        for _value, label in spec.choices:
            model.append(label)

        row = Adw.ComboRow(title=spec.label, model=model)
        current = str(self._action.params.get(spec.key, spec.default))
        if current in values:
            row.set_selected(values.index(current))

        def on_selected(combo: Adw.ComboRow, _param: object) -> None:
            if self._updating:
                return
            self._action.params[spec.key] = values[combo.get_selected()]
            self.refresh_titles()
            self._on_changed()

        row.connect("notify::selected", on_selected)
        return row

    def _build_pick_position_row(self) -> Adw.ActionRow:
        row = Adw.ActionRow(
            title="Pick From Screen",
            subtitle="Point at the position you want and click to capture it",
        )
        row.add_prefix(Gtk.Image(icon_name="find-location-symbolic"))

        button = Gtk.Button(label="Pick…")
        button.set_valign(Gtk.Align.CENTER)

        def on_clicked(_button: Gtk.Button) -> None:
            root = self.get_root()

            def picked(x: int, y: int) -> None:
                self._action.params["x"] = x
                self._action.params["y"] = y
                # Rebuild so the spin rows show the captured values.
                self._build_parameters()
                self.refresh_titles()
                self._on_changed()

            pick_position(root if isinstance(root, Gtk.Window) else None, picked)

        button.connect("clicked", on_clicked)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    def _build_enabled_row(self) -> Adw.SwitchRow:
        row = Adw.SwitchRow(
            title="Enabled",
            subtitle="Turn off to skip this step without deleting it",
        )
        row.set_active(self._action.enabled)

        def on_active(switch: Adw.SwitchRow, _param: object) -> None:
            if self._updating:
                return
            self._action.enabled = switch.get_active()
            self._disabled_badge.set_visible(not self._action.enabled)
            self.refresh_titles()
            self._on_changed()

        row.connect("notify::active", on_active)
        return row

    # --- Presentation ---------------------------------------------------

    def refresh_titles(self) -> None:
        """Update the collapsed summary line.

        Public because the editor re-renders every row's summary after any edit:
        a change to one step can alter how another reads.
        """
        self.set_title(f"{self._index + 1}. {self._action.summary()}")
        self.set_subtitle(self._action.spec.label)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Step {self._index + 1}: {self._action.summary()}"],
        )

    # --- Drag and drop --------------------------------------------------

    def _install_drag_and_drop(self) -> None:
        """Allow reordering by dragging, alongside the Move Up/Down commands."""
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda *_: Gdk.ContentProvider.new_for_value(GObject.Value(int, self._index)),
        )
        self.add_controller(source)

        target = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _target: Gtk.DropTarget, value: Any, _x: float, _y: float) -> bool:
        try:
            source_index = int(value)
        except (TypeError, ValueError):
            return False
        if source_index == self._index:
            return False
        self._on_command(f"move_to:{source_index}", self._index)
        return True


def _number(value: object, fallback: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            return float(fallback)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
