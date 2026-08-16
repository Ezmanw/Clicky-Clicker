"""Recording a macro by performing it.

Input is captured with GTK event controllers while the dialog holds focus,
rather than by reading devices directly.  That has two consequences worth being
explicit about:

* it needs no device permissions at all, so recording works for every user even
  before the ``input`` group is set up; but
* it only captures what is performed **into this dialog**.  Actions carried out
  in another window are not recorded, because a Wayland client receives events
  only for its own focused surface.

For building a macro this is the right trade: the user is composing a sequence,
not shadowing their work in another application.  Timing is captured faithfully,
so the recording plays back at the speed it was performed.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from gi.repository import Adw, Gdk, GLib, Gtk

from ...input.backend import InputEvent, KeyState
from ...macros import MacroRecorder
from ...models import MacroAction, Settings
from ...models import keys as key_table
from ..widgets.rows import plain

__all__ = ["RecorderDialog", "record_macro"]

_GDK_BUTTON_TO_CODE = {
    Gdk.BUTTON_PRIMARY: "BTN_LEFT",
    Gdk.BUTTON_MIDDLE: "BTN_MIDDLE",
    Gdk.BUTTON_SECONDARY: "BTN_RIGHT",
    8: "BTN_SIDE",
    9: "BTN_EXTRA",
    10: "BTN_FORWARD",
    11: "BTN_BACK",
}

_KEYCODE_OFFSET = 8


class RecorderDialog(Adw.Dialog):
    """Captures a sequence of key and button presses with their timing."""

    def __init__(
        self,
        settings: Settings,
        on_finished: Callable[[list[MacroAction]], None],
    ) -> None:
        super().__init__()
        self.set_title("Record Macro")
        self.set_content_width(520)
        self.set_content_height(560)

        self._on_finished = on_finished
        self._recorder = MacroRecorder(
            capture_delays=settings.recording_capture_delays,
            minimum_delay_ms=settings.recording_min_delay_ms,
            # Escape stops the recording, so it must not appear in the result.
            ignored_codes={"KEY_ESC"},
        )
        self._recorder.on_changed = self._on_recorded
        self._preview_rows: list[Adw.ActionRow] = []

        self._build()
        self._install_controllers()
        self._render()

    # --- Construction ---------------------------------------------------

    def _build(self) -> None:
        self._status = Adw.StatusPage(
            icon_name="media-record-symbolic",
            title="Ready to Record",
            description=(
                "Press Start, then perform the keys and clicks you want to "
                "capture in this window. Press Escape to stop."
            ),
        )

        self._start_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="media-record-symbolic", label="Start Recording")
        )
        self._start_button.add_css_class("suggested-action")
        self._start_button.add_css_class("pill")
        self._start_button.set_halign(Gtk.Align.CENTER)
        self._start_button.connect("clicked", lambda *_: self._start())

        self._stop_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="media-playback-stop-symbolic", label="Stop")
        )
        self._stop_button.add_css_class("destructive-action")
        self._stop_button.add_css_class("pill")
        self._stop_button.set_halign(Gtk.Align.CENTER)
        self._stop_button.set_visible(False)
        self._stop_button.connect("clicked", lambda *_: self._stop())

        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        buttons.append(self._start_button)
        buttons.append(self._stop_button)
        self._status.set_child(buttons)

        self._preview_group = Adw.PreferencesGroup(title="Captured Actions")
        preview_page = Adw.PreferencesPage()
        preview_page.add(self._preview_group)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._status, "idle")
        self._stack.add_named(preview_page, "preview")

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Record Macro"))

        discard = Gtk.Button(label="Cancel")
        discard.connect("clicked", lambda *_: self._discard())
        header.pack_start(discard)

        self._save_button = Gtk.Button(label="Add to Macro")
        self._save_button.add_css_class("suggested-action")
        self._save_button.set_sensitive(False)
        self._save_button.connect("clicked", lambda *_: self._save())
        header.pack_end(self._save_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

    def _install_controllers(self) -> None:
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key, True)
        key_controller.connect("key-released", self._on_key_released)
        self.add_controller(key_controller)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_button, True)
        click.connect("released", self._on_button, False)
        self.add_controller(click)

    # --- Capture --------------------------------------------------------

    def _feed(self, code: str, pressed: bool) -> None:
        self._recorder.feed(
            InputEvent(
                code=code,
                state=KeyState.PRESSED if pressed else KeyState.RELEASED,
                timestamp=time.monotonic(),
            )
        )

    def _on_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        _state: Gdk.ModifierType,
        _pressed: bool = True,
    ) -> bool:
        if not self._recorder.is_recording:
            return Gdk.EVENT_PROPAGATE
        if keyval == Gdk.KEY_Escape:
            self._stop()
            return Gdk.EVENT_STOP

        name = key_table.name_for_code(keycode - _KEYCODE_OFFSET)
        if name is not None:
            self._feed(name, True)
        return Gdk.EVENT_STOP

    def _on_key_released(
        self,
        _controller: Gtk.EventControllerKey,
        _keyval: int,
        keycode: int,
        _state: Gdk.ModifierType,
    ) -> None:
        if not self._recorder.is_recording:
            return
        name = key_table.name_for_code(keycode - _KEYCODE_OFFSET)
        if name is not None:
            self._feed(name, False)

    def _on_button(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        pressed: bool = True,
    ) -> None:
        if not self._recorder.is_recording:
            return
        name = _GDK_BUTTON_TO_CODE.get(gesture.get_current_button())
        if name is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._feed(name, pressed)

    # --- State ----------------------------------------------------------

    def _start(self) -> None:
        self._recorder.start()
        self._render()

    def _stop(self) -> None:
        self._recorder.stop()
        self._render()

    def _discard(self) -> None:
        self._recorder.discard()
        self.close()

    def _save(self) -> None:
        actions = self._recorder.actions()
        self.close()
        if actions:
            GLib.idle_add(self._deliver, actions)

    def _deliver(self, actions: list[MacroAction]) -> bool:
        self._on_finished(actions)
        return GLib.SOURCE_REMOVE

    def _on_recorded(self, _count: int) -> None:
        self._render()

    # --- Rendering ------------------------------------------------------

    def _render(self) -> None:
        recording = self._recorder.is_recording
        captured = self._recorder.count

        self._start_button.set_visible(not recording)
        self._stop_button.set_visible(recording)
        self._save_button.set_sensitive(not recording and captured > 0)

        if recording:
            self._status.set_title(f"Recording — {captured} action(s)")
            self._status.set_description(
                "Perform the keys and clicks you want to capture, here in this "
                "window. Press Escape or Stop when you are finished."
            )
        elif captured:
            self._status.set_title(f"Captured {captured} action(s)")
            self._status.set_description("Add them to the macro, or record again to replace them.")
        else:
            self._status.set_title("Ready to Record")
            self._status.set_description(
                "Press Start, then perform the keys and clicks you want to "
                "capture in this window. Press Escape to stop."
            )

        self._render_preview()
        self._stack.set_visible_child_name(
            "preview" if captured and not recording else "idle"
        )

    def _render_preview(self) -> None:
        for row in self._preview_rows:
            self._preview_group.remove(row)
        self._preview_rows = []

        if self._recorder.is_recording:
            return

        for index, action in enumerate(self._recorder.actions()):
            row = plain(Adw.ActionRow(), title=f"{index + 1}. {action.summary()}")
            row.set_subtitle(action.spec.label)
            row.add_prefix(Gtk.Image(icon_name=action.spec.icon_name))
            self._preview_group.add(row)
            self._preview_rows.append(row)


def record_macro(
    parent: Gtk.Widget,
    settings: Settings,
    on_finished: Callable[[list[MacroAction]], None],
) -> RecorderDialog:
    """Open the recorder anchored to *parent*."""
    dialog = RecorderDialog(settings, on_finished)
    dialog.present(parent)
    return dialog
