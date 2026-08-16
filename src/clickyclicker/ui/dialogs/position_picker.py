"""Picking a screen coordinate by pointing at it.

Wayland gives a client no way to ask where the pointer is globally -- that is
one of the restrictions the protocol exists to impose, and there is no portal
for it.  What a client *can* do is observe the pointer inside its own surface.

So the picker opens a fullscreen window and reads pointer motion within it.
Fullscreen means the window's coordinate space is the monitor's, and the
monitor's position in the desktop layout is available from ``GdkMonitor``, so
adding the two gives a true desktop coordinate.  The pointer never leaves the
application's own surface, which is precisely why this is allowed.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, Gtk

__all__ = ["PositionPickerWindow", "pick_position", "desktop_size"]


class PositionPickerWindow(Gtk.Window):
    """Fullscreen overlay that reports the coordinate the user clicks."""

    def __init__(
        self,
        parent: Gtk.Window | None,
        on_picked: Callable[[int, int], None],
    ) -> None:
        super().__init__()
        self._on_picked = on_picked
        self._origin = (0, 0)
        self._last = (0, 0)

        self.set_modal(True)
        if parent is not None:
            self.set_transient_for(parent)
        self.set_title("Pick a Screen Position")

        self._status = Adw.StatusPage(
            icon_name="find-location-symbolic",
            title="Move the pointer, then click",
            description="Press Escape to cancel without choosing a position.",
        )

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title="Pick a Screen Position"))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._status)
        self.set_child(toolbar)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

        escape = Gtk.EventControllerKey()
        escape.connect("key-pressed", self._on_key)
        self.add_controller(escape)

        self.connect("map", self._on_mapped)
        self.fullscreen()

    def _on_mapped(self, *_args: object) -> None:
        """Record where this monitor sits in the desktop layout.

        Only knowable once mapped, because that is when the surface is
        associated with a monitor.
        """
        surface = self.get_surface()
        display = self.get_display()
        if surface is None or display is None:
            return
        monitor = display.get_monitor_at_surface(surface)
        if monitor is not None:
            geometry = monitor.get_geometry()
            self._origin = (geometry.x, geometry.y)

    def _on_motion(self, _controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
        self._last = (int(self._origin[0] + x), int(self._origin[1] + y))
        self._status.set_title(f"X {self._last[0]}, Y {self._last[1]}")

    def _on_pressed(self, _gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        self._last = (int(self._origin[0] + x), int(self._origin[1] + y))
        picked = self._last
        self.close()
        self._on_picked(*picked)

    def _on_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE


def pick_position(
    parent: Gtk.Window | None, on_picked: Callable[[int, int], None]
) -> PositionPickerWindow:
    """Open the picker and call *on_picked* with the chosen desktop coordinate."""
    window = PositionPickerWindow(parent, on_picked)
    window.present()
    return window


def desktop_size(display: Gdk.Display | None = None) -> tuple[int, int]:
    """The bounding size of the whole desktop, across every monitor.

    Reported to the daemon so it can scale absolute pointer coordinates: the
    daemon has no display connection and cannot work this out for itself.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return (1920, 1080)

    monitors = display.get_monitors()
    right = bottom = 0
    for index in range(monitors.get_n_items()):
        monitor = monitors.get_item(index)
        if monitor is None:
            continue
        geometry = monitor.get_geometry()
        right = max(right, geometry.x + geometry.width)
        bottom = max(bottom, geometry.y + geometry.height)

    return (right or 1920, bottom or 1080)
