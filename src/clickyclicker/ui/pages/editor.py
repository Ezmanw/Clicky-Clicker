"""The visual macro editor.

Two views behind an ``AdwViewSwitcher``:

**Actions** -- the ordered list of steps, each an
:class:`~clickyclicker.ui.widgets.action_row.ActionEditorRow`.  Building a macro
here never involves typing syntax: steps come from a menu and their parameters
from spin rows, switches and pickers.

**Playback** -- how the macro repeats, how long it waits between repeats, and
what the trigger does.  Those two settings interact, so the page states the
resulting behaviour in a sentence instead of leaving the user to work out the
combination.

Edits are applied to the model immediately and written to disk on a short
delay, so there is no Save button to forget and no unsaved-changes dialog to
dismiss.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from ...macros import Severity, inspect
from ...models import Macro, PlaybackMode, TriggerMode
from ...models.action import ACTION_SPECS, ActionType, MacroAction, action_types_in_order
from ...models.macro import PLAYBACK_LABELS, TRIGGER_LABELS
from ...services import MacroLibrary
from ..widgets.action_row import ActionEditorRow
from ..widgets.rows import escape, plain

__all__ = ["MacroEditorPage"]

#: How long to wait after the last edit before writing to disk.  Long enough
#: that dragging a spin row does not cause a write per increment, short enough
#: that closing the window straight after an edit still saves it.
_SAVE_DELAY_MS = 400


class MacroEditorPage(Adw.NavigationPage):
    """Edits one macro."""

    def __init__(
        self,
        library: MacroLibrary,
        macro: Macro,
        *,
        on_test: Callable[[Macro], None],
        on_stop: Callable[[], None],
        on_record: Callable[[Macro], None],
        on_toast: Callable[..., None],
    ) -> None:
        """
        :param on_toast: called as ``on_toast(message)``, or
            ``on_toast(message, undo)`` to offer an Undo button.
        """
        super().__init__()
        self._library = library
        self._macro = macro
        self._on_test = on_test
        self._on_stop = on_stop
        self._on_record = on_record
        self._on_toast = on_toast
        self._save_source: int | None = None
        self._rows: list[ActionEditorRow] = []
        self._updating = False

        self.set_title(escape(macro.name))
        self.set_tag(f"editor:{macro.id}")

        self._actions_group = Adw.PreferencesGroup()
        self._banner = plain(Adw.Banner())
        self._banner.set_revealed(False)

        self._build()
        self.refresh()

        self.connect("hidden", lambda *_: self.flush())

    @property
    def macro(self) -> Macro:
        """The macro being edited."""
        return self._macro

    # --- Construction ---------------------------------------------------

    def _build(self) -> None:
        self._stack = Adw.ViewStack()
        self._stack.add_titled_with_icon(
            self._build_actions_view(), "actions", "Actions", "view-list-ordered-symbolic"
        )
        self._stack.add_titled_with_icon(
            self._build_playback_view(), "playback", "Playback", "media-playlist-repeat-symbolic"
        )

        switcher = Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)

        self._test_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Test")
        )
        self._test_button.set_tooltip_text("Run this macro once to try it out")
        self._test_button.connect("clicked", lambda *_: self._on_test(self._macro))
        header.pack_end(self._test_button)

        self._stop_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="media-playback-stop-symbolic", label="Stop")
        )
        self._stop_button.add_css_class("destructive-action")
        self._stop_button.set_tooltip_text("Stop every running macro")
        self._stop_button.connect("clicked", lambda *_: self._on_stop())
        self._stop_button.set_visible(False)
        header.pack_end(self._stop_button)

        record = Gtk.Button(icon_name="media-record-symbolic")
        record.set_tooltip_text("Record actions from your keyboard and mouse")
        record.update_property([Gtk.AccessibleProperty.LABEL], ["Record a macro"])
        record.connect("clicked", lambda *_: self._on_record(self._macro))
        header.pack_start(record)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self._banner)
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    def _build_actions_view(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        details = Adw.PreferencesGroup(title="Macro")
        self._name_row = Adw.EntryRow(title="Name")
        self._name_row.set_text(self._macro.name)
        self._name_row.connect("changed", self._on_name_changed)
        details.add(self._name_row)

        self._description_row = Adw.EntryRow(title="Description")
        self._description_row.set_text(self._macro.description)
        self._description_row.connect("changed", self._on_description_changed)
        details.add(self._description_row)
        page.add(details)

        self._actions_group.set_title("Actions")
        self._actions_group.set_description(
            "Steps run from top to bottom. Drag a step, or use its arrows, to reorder."
        )
        self._actions_group.set_header_suffix(self._build_add_menu())
        page.add(self._actions_group)

        commands = Adw.PreferencesGroup()
        clear = Gtk.Button(label="Clear All Actions")
        clear.add_css_class("destructive-action")
        clear.set_halign(Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._on_clear())
        commands.add(clear)
        page.add(commands)

        return page

    def _build_add_menu(self) -> Gtk.Widget:
        """The Add Action button, grouped by category."""
        button = Gtk.MenuButton()
        button.set_child(
            Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Action")
        )
        button.add_css_class("flat")
        button.set_tooltip_text("Append a step to this macro")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        current_category = ""
        for action_type in action_types_in_order():
            spec = ACTION_SPECS[action_type]
            if spec.category != current_category:
                current_category = spec.category
                label = Gtk.Label(label=spec.category, xalign=0.0)
                label.add_css_class("dim-label")
                label.add_css_class("caption-heading")
                label.set_margin_top(6)
                box.append(label)

            entry = Gtk.Button()
            entry.add_css_class("flat")
            entry.set_child(Adw.ButtonContent(icon_name=spec.icon_name, label=spec.label))
            entry.connect(
                "clicked",
                lambda _b, chosen=action_type: (
                    popover.popdown(),
                    self._append_action(chosen),
                ),
            )
            box.append(entry)

        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=460)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(box)
        popover.set_child(scroller)
        button.set_popover(popover)
        return button

    def _build_playback_view(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        playback = Adw.PreferencesGroup(
            title="Playback",
            description="How many times the macro repeats once it starts.",
        )

        self._playback_modes = list(PlaybackMode)
        model = Gtk.StringList()
        for mode in self._playback_modes:
            model.append(PLAYBACK_LABELS[mode])
        self._playback_row = Adw.ComboRow(title="Mode", model=model)
        self._playback_row.connect("notify::selected", self._on_playback_changed)
        playback.add(self._playback_row)

        self._repeat_row = Adw.SpinRow(
            title="Repeat Count",
            subtitle="How many passes to make",
            adjustment=Gtk.Adjustment(lower=1, upper=100_000, step_increment=1, page_increment=10),
        )
        self._repeat_row.connect("notify::value", self._on_repeat_changed)
        playback.add(self._repeat_row)

        self._gap_row = Adw.SpinRow(
            title="Repeat Gap",
            subtitle="Delay between repeats, separate from any Wait action inside the macro",
            adjustment=Gtk.Adjustment(
                lower=0, upper=3_600_000, step_increment=1, page_increment=50
            ),
        )
        self._gap_row.connect("notify::value", self._on_gap_changed)
        playback.add(self._gap_row)
        page.add(playback)

        trigger = Adw.PreferencesGroup(
            title="Trigger",
            description="What activating the bound input does.",
        )
        self._trigger_modes = list(TriggerMode)
        trigger_model = Gtk.StringList()
        for mode in self._trigger_modes:
            trigger_model.append(TRIGGER_LABELS[mode])
        self._trigger_row = plain(Adw.ComboRow(title="Behaviour", model=trigger_model))
        self._trigger_row.connect("notify::selected", self._on_trigger_changed)
        trigger.add(self._trigger_row)
        page.add(trigger)

        summary = Adw.PreferencesGroup(title="Resulting Behaviour")
        self._summary_row = plain(Adw.ActionRow())
        self._summary_row.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
        self._summary_row.set_title("")
        self._summary_row.set_title_lines(0)
        summary.add(self._summary_row)

        self._bindings_row = plain(Adw.ActionRow(title="Assigned To"))
        self._bindings_row.add_prefix(Gtk.Image(icon_name="input-keyboard-symbolic"))
        summary.add(self._bindings_row)
        page.add(summary)

        return page

    # --- Rendering ------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild every view from the macro."""
        self._updating = True
        try:
            self.set_title(escape(self._macro.name))
            self._render_actions()
            self._render_playback()
            self._render_validation()
        finally:
            self._updating = False

    def _render_actions(self) -> None:
        for row in self._rows:
            self._actions_group.remove(row)
        self._rows = []

        if not self._macro.actions:
            self._empty_row = plain(Adw.ActionRow(
                title="No actions yet",
                subtitle="Use Add Action to build the macro, or record one from your keyboard.",
            ))
            self._empty_row.add_prefix(Gtk.Image(icon_name="list-add-symbolic"))
            self._actions_group.add(self._empty_row)
            self._rows = []
            return

        total = len(self._macro.actions)
        for index, action in enumerate(self._macro.actions):
            row = ActionEditorRow(
                action,
                index,
                total=total,
                on_changed=self._on_action_edited,
                on_command=self._on_action_command,
            )
            self._actions_group.add(row)
            self._rows.append(row)

    def _render_playback(self) -> None:
        playback = self._macro.playback
        self._playback_row.set_selected(self._playback_modes.index(playback.mode))
        self._repeat_row.set_value(playback.repeat_count)
        self._gap_row.set_value(playback.gap_ms)
        self._trigger_row.set_selected(self._trigger_modes.index(self._macro.trigger.mode))

        # Repeat count only means something in the counted mode.
        self._repeat_row.set_sensitive(playback.mode is PlaybackMode.REPEAT_COUNT)
        # Playback modes that pin the trigger make the trigger row misleading.
        pinned = playback.mode in (PlaybackMode.WHILE_HELD, PlaybackMode.TOGGLE)
        self._trigger_row.set_sensitive(not pinned)
        self._trigger_row.set_subtitle(
            f"Set by the playback mode to “{TRIGGER_LABELS[self._macro.effective_trigger()]}”"
            if pinned
            else ""
        )

        bindings = self._library.bindings_for(self._macro.id)
        trigger_label = bindings[0].input_label() if bindings else "the trigger"
        self._summary_row.set_title(self._macro.describe_behaviour(trigger_label))

        if bindings:
            self._bindings_row.set_subtitle(
                ", ".join(binding.input_label() for binding in bindings)
            )
        else:
            self._bindings_row.set_subtitle(
                "Not assigned to any input yet — add one on the Mappings page"
            )

    def _render_validation(self) -> None:
        issues = inspect(self._macro)
        if not issues:
            self._banner.set_revealed(False)
            return

        worst = issues[0]
        self._banner.set_title(worst.message)
        self._banner.add_css_class(
            "error" if worst.severity is Severity.ERROR else "warning"
        )
        self._banner.set_revealed(True)

    def set_running(self, running: bool) -> None:
        """Swap Test for Stop while this macro is playing."""
        self._test_button.set_visible(not running)
        self._stop_button.set_visible(running)

    # --- Editing --------------------------------------------------------

    def _append_action(self, action_type: ActionType) -> None:
        self._macro.actions.append(MacroAction.create(action_type))
        self._render_actions()
        self._render_validation()
        self._schedule_save()

    def _on_action_edited(self) -> None:
        if self._updating:
            return
        # Summaries on other rows can depend on this edit, and the validation
        # banner certainly does.
        for row in self._rows:
            row.refresh_titles()
        self._render_playback()
        self._render_validation()
        self._schedule_save()

    def _on_action_command(self, command: str, index: int) -> None:
        actions = self._macro.actions
        if not 0 <= index < len(actions):
            return

        if command.startswith("move_to:"):
            source = int(command.split(":", 1)[1])
            if 0 <= source < len(actions) and source != index:
                actions.insert(index, actions.pop(source))
        elif command == "delete":
            removed = actions.pop(index)
            self._offer_undo(removed, index)
        elif command == "duplicate":
            actions.insert(index + 1, actions[index].duplicate())
        elif command == "move_up" and index > 0:
            actions[index - 1], actions[index] = actions[index], actions[index - 1]
        elif command == "move_down" and index < len(actions) - 1:
            actions[index + 1], actions[index] = actions[index], actions[index + 1]
        elif command == "insert_above":
            actions.insert(index, MacroAction.create(ActionType.WAIT))
        elif command == "insert_below":
            actions.insert(index + 1, MacroAction.create(ActionType.WAIT))
        else:
            return

        self._render_actions()
        self._render_playback()
        self._render_validation()
        self._schedule_save()

    def _offer_undo(self, action: MacroAction, index: int) -> None:
        """Deleting a step is undoable, so it needs no confirmation dialog."""

        def restore() -> None:
            self._macro.actions.insert(min(index, len(self._macro.actions)), action)
            self._render_actions()
            self._render_validation()
            self._schedule_save()

        self._on_toast(f"Removed {action.summary()}", restore)

    def _on_clear(self) -> None:
        if not self._macro.actions:
            return
        dialog = Adw.AlertDialog(
            heading="Clear All Actions?",
            body=f"Every step in “{self._macro.name}” will be removed. This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def responded(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "clear":
                self._macro.actions = []
                self._render_actions()
                self._render_validation()
                self._schedule_save()

        dialog.connect("response", responded)
        dialog.present(self)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        if self._updating:
            return
        text = entry.get_text().strip()
        if text:
            self._macro.name = text
            self.set_title(escape(text))
            self._schedule_save()

    def _on_description_changed(self, entry: Adw.EntryRow) -> None:
        if self._updating:
            return
        self._macro.description = entry.get_text()
        self._schedule_save()

    def _on_playback_changed(self, combo: Adw.ComboRow, _param: object) -> None:
        if self._updating:
            return
        self._macro.playback.mode = self._playback_modes[combo.get_selected()]
        self._render_playback()
        self._render_validation()
        self._schedule_save()

    def _on_repeat_changed(self, spin: Adw.SpinRow, _param: object) -> None:
        if self._updating:
            return
        self._macro.playback.repeat_count = int(spin.get_value())
        self._render_playback()
        self._schedule_save()

    def _on_gap_changed(self, spin: Adw.SpinRow, _param: object) -> None:
        if self._updating:
            return
        self._macro.playback.gap_ms = int(spin.get_value())
        self._render_playback()
        self._render_validation()
        self._schedule_save()

    def _on_trigger_changed(self, combo: Adw.ComboRow, _param: object) -> None:
        if self._updating:
            return
        self._macro.trigger.mode = self._trigger_modes[combo.get_selected()]
        self._render_playback()
        self._schedule_save()

    def append_actions(self, actions: list[MacroAction]) -> None:
        """Append recorded actions and show them."""
        self._macro.actions.extend(actions)
        self._render_actions()
        self._render_validation()
        self._schedule_save()

    # --- Saving ---------------------------------------------------------

    def _schedule_save(self) -> None:
        """Coalesce rapid edits into one write."""
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(_SAVE_DELAY_MS, self._save)

    def _save(self) -> bool:
        self._save_source = None
        try:
            self._library.save_macro(self._macro)
        except OSError as exc:
            self._on_toast(f"Could not save “{self._macro.name}”: {exc}")
        return GLib.SOURCE_REMOVE

    def flush(self) -> None:
        """Write any pending edit immediately, e.g. when navigating away."""
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
            self._save_source = None
            self._save()
