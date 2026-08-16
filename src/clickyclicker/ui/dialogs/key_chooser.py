"""Choosing a key or mouse button.

Two ways in, because neither alone is sufficient:

**Press it.**  The quickest way to answer "which button is my thumb button?".
Implemented with GTK event controllers rather than by reading devices, so it
needs no special permission and works for any user.  On Wayland the compositor
keeps a few combinations for itself -- Super, and whatever the desktop has bound
-- so those cannot be captured this way; the dialog says so rather than
appearing broken.

**Browse it.**  A searchable, categorised list covering everything the kernel
can express, including buttons a mouse may not have but a preset might use.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gdk, GLib, Gtk

from ...models import keys
from ...models.keys import KeyInfo
from ..widgets.rows import plain

__all__ = ["KeyChooserDialog", "choose_key"]

#: GDK reports pointer buttons using the X11 numbering that Wayland clients
#: inherited; the kernel numbers them differently.  Buttons past this table are
#: reachable by browsing rather than by pressing.
_GDK_BUTTON_TO_CODE = {
    Gdk.BUTTON_PRIMARY: "BTN_LEFT",
    Gdk.BUTTON_MIDDLE: "BTN_MIDDLE",
    Gdk.BUTTON_SECONDARY: "BTN_RIGHT",
    8: "BTN_SIDE",
    9: "BTN_EXTRA",
    10: "BTN_FORWARD",
    11: "BTN_BACK",
}

#: GDK keeps the X11 convention of offsetting hardware keycodes by 8, so the
#: kernel code is recovered by subtracting it.  True on both the Wayland and
#: X11 GDK backends.
_KEYCODE_OFFSET = 8


class KeyChooserDialog(Adw.Dialog):
    """Modal chooser returning a kernel symbol such as ``KEY_E`` or ``BTN_SIDE``."""

    def __init__(
        self,
        *,
        title: str = "Choose Input",
        selected: str | None = None,
        keyboard: bool = True,
        mouse: bool = True,
        on_chosen: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.set_title(title)
        self.set_content_width(480)
        self.set_content_height(560)

        self._on_chosen = on_chosen
        self._selected = selected
        self._allow_keyboard = keyboard
        self._allow_mouse = mouse
        self._capturing = False
        self._rows: list[tuple[Gtk.Widget, KeyInfo]] = []

        self._search = Gtk.SearchEntry(placeholder_text="Search keys and buttons")
        self._search.connect("search-changed", lambda *_: self._apply_filter())

        self._capture_button = Gtk.Button(
            child=Adw.ButtonContent(
                icon_name="input-keyboard-symbolic", label="Press an Input"
            )
        )
        self._capture_button.add_css_class("suggested-action")
        self._capture_button.set_tooltip_text(
            "Detect an input by pressing the key or button you want to use"
        )
        self._capture_button.connect("clicked", self._on_capture_clicked)

        self._capture_status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._capture_status.add_css_class("dim-label")
        self._capture_status.set_visible(False)

        self._list_page = Adw.PreferencesPage()
        self._empty = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title="No Matches",
            description="No key or button matches your search.",
        )
        self._empty.set_visible(False)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._list_page, "list")
        self._stack.add_named(self._empty, "empty")

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        controls.set_margin_top(12)
        controls.set_margin_bottom(6)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        controls.append(self._search)
        controls.append(self._capture_button)
        controls.append(self._capture_status)
        toolbar.add_top_bar(controls)

        toolbar.set_content(self._stack)
        self.set_child(toolbar)

        self._build_rows()
        self._install_capture_controllers()

    # --- Construction ---------------------------------------------------

    def _build_rows(self) -> None:
        """Populate the categorised list once; filtering only toggles visibility."""
        for category, entries in keys.by_category().items():
            visible = [
                info
                for info in entries
                if (info.kind is keys.KeyKind.MOUSE and self._allow_mouse)
                or (info.kind is keys.KeyKind.KEYBOARD and self._allow_keyboard)
            ]
            if not visible:
                continue

            group = Adw.PreferencesGroup(title=category)
            for info in visible:
                row = plain(Adw.ActionRow(), title=info.label, subtitle=info.name)
                row.set_activatable(True)
                row.connect("activated", self._on_row_activated, info)
                if info.name == self._selected:
                    tick = Gtk.Image(icon_name="object-select-symbolic")
                    tick.set_tooltip_text("Currently selected")
                    row.add_suffix(tick)
                group.add(row)
                self._rows.append((row, info))
            self._list_page.add(group)

    def _install_capture_controllers(self) -> None:
        """Listen for the next key or button press once capture is armed."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        # Capture phase, so a keypress is intercepted before the search entry or
        # a focused row consumes it.
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(key_controller)

        click_controller = Gtk.GestureClick()
        click_controller.set_button(0)  # every button, not just primary
        click_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click_controller.connect("pressed", self._on_button_pressed)
        self.add_controller(click_controller)

    # --- Capture --------------------------------------------------------

    def _on_capture_clicked(self, _button: Gtk.Button) -> None:
        self._capturing = True
        self._capture_button.set_sensitive(False)
        self._capture_status.set_visible(True)
        self._capture_status.set_label(
            "Press any key or mouse button now. Press Escape to cancel.\n"
            "Shortcuts reserved by the desktop, such as Super, cannot be "
            "detected here — choose those from the list below."
        )

    def _end_capture(self) -> None:
        self._capturing = False
        self._capture_button.set_sensitive(True)
        self._capture_status.set_visible(False)

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if not self._capturing:
            return Gdk.EVENT_PROPAGATE
        if keyval == Gdk.KEY_Escape:
            self._end_capture()
            return Gdk.EVENT_STOP
        if not self._allow_keyboard:
            return Gdk.EVENT_STOP

        name = keys.name_for_code(keycode - _KEYCODE_OFFSET)
        if name is None:
            self._capture_status.set_label(
                "That key was not recognised. Please choose it from the list below."
            )
            return Gdk.EVENT_STOP

        self._end_capture()
        self._choose(name)
        return Gdk.EVENT_STOP

    def _on_button_pressed(
        self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float
    ) -> None:
        if not self._capturing:
            return
        if not self._allow_mouse:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        button = gesture.get_current_button()
        name = _GDK_BUTTON_TO_CODE.get(button)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if name is None:
            self._capture_status.set_label(
                f"Mouse button {button} was not recognised. "
                "Please choose it from the list below."
            )
            return

        self._end_capture()
        self._choose(name)

    # --- Selection ------------------------------------------------------

    def _on_row_activated(self, _row: Adw.ActionRow, info: KeyInfo) -> None:
        self._choose(info.name)

    def _choose(self, name: str) -> None:
        if self._on_chosen is not None:
            # Defer until the dialog has closed, so a caller that opens another
            # dialog in response is not fighting this one for the presentation.
            GLib.idle_add(self._deliver, name, priority=GLib.PRIORITY_DEFAULT_IDLE)
        self.close()

    def _deliver(self, name: str) -> bool:
        if self._on_chosen is not None:
            self._on_chosen(name)
        return GLib.SOURCE_REMOVE

    # --- Filtering ------------------------------------------------------

    def _apply_filter(self) -> None:
        needle = self._search.get_text().strip().casefold()
        matches = 0
        for row, info in self._rows:
            visible = (
                not needle
                or needle in info.label.casefold()
                or needle in info.name.casefold()
            )
            row.set_visible(visible)
            matches += visible

        for group in _preference_groups(self._list_page):
            group.set_visible(
                any(
                    row.get_visible()
                    for row, _info in self._rows
                    if row.get_ancestor(Adw.PreferencesGroup) is group
                )
            )
        self._stack.set_visible_child_name("list" if matches else "empty")


def _preference_groups(page: Adw.PreferencesPage) -> list[Adw.PreferencesGroup]:
    """Every group in *page*.

    ``AdwPreferencesPage`` exposes no accessor for its groups, so this walks the
    widget tree instead.
    """
    found: list[Adw.PreferencesGroup] = []

    def walk(widget: Gtk.Widget | None) -> None:
        child = widget.get_first_child() if widget else None
        while child is not None:
            if isinstance(child, Adw.PreferencesGroup):
                found.append(child)
            else:
                walk(child)
            child = child.get_next_sibling()

    walk(page)
    return found


def choose_key(
    parent: Gtk.Widget,
    *,
    title: str = "Choose Input",
    selected: str | None = None,
    keyboard: bool = True,
    mouse: bool = True,
    on_chosen: Callable[[str], None],
) -> KeyChooserDialog:
    """Open a :class:`KeyChooserDialog` anchored to *parent*."""
    dialog = KeyChooserDialog(
        title=title,
        selected=selected,
        keyboard=keyboard,
        mouse=mouse,
        on_chosen=on_chosen,
    )
    dialog.present(parent)
    return dialog
