"""Backend-neutral interfaces for reading and injecting input.

The rest of the application talks to input only through :class:`InputSource`
and :class:`InputSink`.  The evdev implementation in
:mod:`clickyclicker.input.evdev_backend` and
:mod:`clickyclicker.input.uinput_sink` is the only one shipped today, but the
executor, the recorder and the daemon engine are written against these
interfaces so a future backend -- a compositor portal, say, if Wayland ever
grows one -- can be dropped in without touching them.

Why evdev and uinput specifically
---------------------------------
Wayland compositors, unlike X11, do not let an ordinary client read the global
input stream or warp the pointer.  That is a deliberate security property, not
an oversight, and there is no portal for it.  The supported way to do this on
Linux is below the display server entirely: read the kernel's evdev devices and
inject through a kernel-level virtual device (uinput).  This works identically
on GNOME, COSMIC, KDE, Sway and on X11, and it needs no compositor cooperation.
The cost is that it requires device permissions, which is why the daemon exists
and why the README leads with the group setup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import IntEnum

__all__ = ["KeyState", "InputEvent", "DeviceInfo", "InputSource", "InputSink"]


class KeyState(IntEnum):
    """Value carried by an ``EV_KEY`` event, matching the kernel's encoding."""

    RELEASED = 0
    PRESSED = 1
    HELD = 2
    """Auto-repeat.  The engine ignores these; a held key should not re-trigger."""


@dataclass(frozen=True, slots=True)
class InputEvent:
    """A key or button transition observed on a physical device."""

    code: str
    """Kernel symbol, e.g. ``KEY_E`` or ``BTN_SIDE``."""

    state: KeyState
    timestamp: float
    """Monotonic seconds, used by the recorder to reconstruct real timing."""

    device_id: str = ""
    """Stable identifier of the originating device."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """A physical input device the backend can read from."""

    id: str
    """Stable identifier, derived from vendor/product/name rather than the
    ``/dev/input/eventN`` number, which is not stable across reboots."""

    name: str
    path: str
    has_keyboard: bool
    has_mouse: bool
    vendor: int = 0
    product: int = 0

    @property
    def kind_label(self) -> str:
        """Short description used in the device list."""
        if self.has_keyboard and self.has_mouse:
            return "Keyboard and pointer"
        if self.has_keyboard:
            return "Keyboard"
        if self.has_mouse:
            return "Pointer"
        return "Other input device"

    @property
    def icon_name(self) -> str:
        """A standard Adwaita icon name for this device kind."""
        if self.has_mouse and not self.has_keyboard:
            return "input-mouse-symbolic"
        if self.has_keyboard:
            return "input-keyboard-symbolic"
        return "input-dialpad-symbolic"


class InputSource(ABC):
    """Reads key and button transitions from physical devices."""

    @abstractmethod
    def list_devices(self) -> list[DeviceInfo]:
        """Enumerate readable devices.

        :raises PermissionDeniedError: if the device directory cannot be read.
        """

    @abstractmethod
    def listen(self, on_event: Callable[[InputEvent], None]) -> None:
        """Begin delivering events to *on_event* until :meth:`stop` is called.

        Blocks the calling thread.  ``on_event`` runs on that thread, so
        implementations of it must not block.
        """

    @abstractmethod
    def stop(self) -> None:
        """Ask :meth:`listen` to return.  Safe to call from another thread."""

    @abstractmethod
    def set_suppressed(self, codes: dict[str, set[str] | None]) -> None:
        """Declare which codes must be withheld from the rest of the session.

        :param codes: maps a device id (or ``"*"`` for any device) to the set of
            kernel symbols to swallow.  A value of ``None`` suppresses nothing
            for that device.

        Suppression requires an exclusive grab, so a backend may need to reopen
        devices when this changes.
        """


class InputSink(ABC):
    """Injects synthetic input into the session."""

    @abstractmethod
    def open(self) -> None:
        """Create the virtual devices.

        :raises UinputUnavailableError: if ``/dev/uinput`` cannot be used.
        """

    @abstractmethod
    def close(self) -> None:
        """Destroy the virtual devices, releasing anything still held."""

    @abstractmethod
    def key(self, code: str, pressed: bool) -> None:
        """Press or release a key or mouse button by kernel symbol."""

    @abstractmethod
    def move_absolute(self, x: int, y: int) -> None:
        """Move the pointer to a pixel position on the desktop."""

    @abstractmethod
    def move_relative(self, dx: int, dy: int) -> None:
        """Move the pointer by a pixel offset."""

    @abstractmethod
    def scroll(self, amount: int, horizontal: bool = False) -> None:
        """Turn the scroll wheel by *amount* detents."""

    @abstractmethod
    def release_all(self) -> None:
        """Release every key this sink is currently holding down.

        Called whenever a macro stops for any reason.  Without it, a macro
        interrupted between a press and its release would leave a key stuck
        down for the whole session.
        """

    @abstractmethod
    def held_codes(self) -> Iterator[str]:
        """The kernel symbols currently held down by this sink."""
