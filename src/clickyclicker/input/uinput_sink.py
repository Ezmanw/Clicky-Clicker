"""Input injection through the kernel's ``uinput`` virtual device interface.

Three virtual devices are created rather than one, because ``libinput``
classifies a device by the capabilities it advertises and a single device
claiming keys, relative motion *and* absolute motion is classified as none of
them cleanly:

``Clicky Clicker Keyboard``
    ``EV_KEY`` over the ``KEY_*`` range.
``Clicky Clicker Mouse``
    ``EV_KEY`` over the ``BTN_*`` range plus ``EV_REL`` motion and scrolling.
``Clicky Clicker Pointer``
    ``EV_ABS`` motion with ``INPUT_PROP_POINTER``, mirroring the capability set
    of a QEMU/VMware USB tablet.  That shape is what makes absolute pointer
    positioning work on Wayland: compositors already map absolute pointing
    devices onto the desktop, so the pointer jumps to the requested coordinate
    without needing a pointer-warp API that Wayland does not offer.

All three sit below the display server, so the same code drives GNOME, COSMIC,
KDE, Sway and X11 identically.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from typing import Any

from ..models import keys
from .backend import InputSink
from .errors import BackendUnavailableError, UinputUnavailableError

log = logging.getLogger(__name__)

__all__ = ["VIRTUAL_DEVICE_PREFIX", "ABS_RANGE", "UinputSink"]

#: Every virtual device this application creates is named with this prefix, so
#: :mod:`clickyclicker.input.evdev_backend` can refuse to read from them.
#: Without that check the sink would feed its own output back into the source
#: and a single keypress would loop forever.
VIRTUAL_DEVICE_PREFIX = "Clicky Clicker"

#: Logical resolution of the absolute pointer.  The compositor scales this onto
#: the desktop, so it is a fixed grid rather than a pixel count.
ABS_RANGE = 32767

_VENDOR = 0x1D6B  # Linux Foundation, the conventional id for virtual devices.
_PRODUCT = 0x0C1C
_VERSION = 1

#: Fallback desktop size used only if the interface never reported one.  The
#: daemon has no display connection of its own, so the UI tells it the real
#: geometry on connect; see :meth:`UinputSink.set_screen_size`.
_DEFAULT_SCREEN = (1920, 1080)


def _load_evdev() -> tuple[Any, Any, Any]:
    """Import python-evdev, translating absence into a typed error."""
    try:
        from evdev import AbsInfo, UInput, ecodes  # noqa: PLC0415 - optional dependency
    except ImportError as exc:  # pragma: no cover - depends on host packages
        raise BackendUnavailableError(str(exc)) from exc
    return UInput, ecodes, AbsInfo


class UinputSink(InputSink):
    """Sends synthetic key, button, pointer and scroll events."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._keyboard: Any = None
        self._mouse: Any = None
        self._pointer: Any = None
        self._held: set[str] = set()
        self._screen: tuple[int, int] = _DEFAULT_SCREEN
        self._ecodes: Any = None

    # --- Lifecycle ------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether the virtual devices currently exist."""
        return self._keyboard is not None

    def open(self) -> None:
        """Create the three virtual devices.

        :raises UinputUnavailableError: if ``/dev/uinput`` is missing, not
            loaded, or not writable by this user.
        :raises BackendUnavailableError: if python-evdev is not installed.
        """
        with self._lock:
            if self.is_open:
                return

            UInput, ecodes, AbsInfo = _load_evdev()
            self._ecodes = ecodes

            # Derived from the generated table rather than a numeric range: the
            # KEY_ range is not contiguous (it resumes above the BTN_ block at
            # 0x160), so a range filter would silently drop valid keys.
            key_codes = _codes_with_prefix("KEY_", ecodes.KEY_MAX)
            button_codes = _codes_with_prefix("BTN_", ecodes.KEY_MAX)

            try:
                self._keyboard = UInput(
                    {ecodes.EV_KEY: key_codes},
                    name=f"{VIRTUAL_DEVICE_PREFIX} Keyboard",
                    vendor=_VENDOR,
                    product=_PRODUCT,
                    version=_VERSION,
                    bustype=ecodes.BUS_VIRTUAL,
                )
                self._mouse = UInput(
                    {
                        ecodes.EV_KEY: button_codes,
                        ecodes.EV_REL: [
                            ecodes.REL_X,
                            ecodes.REL_Y,
                            ecodes.REL_WHEEL,
                            ecodes.REL_HWHEEL,
                        ],
                    },
                    name=f"{VIRTUAL_DEVICE_PREFIX} Mouse",
                    vendor=_VENDOR,
                    product=_PRODUCT + 1,
                    version=_VERSION,
                    bustype=ecodes.BUS_VIRTUAL,
                )
                self._pointer = self._create_absolute_pointer(UInput, ecodes, AbsInfo)
            except (PermissionError, FileNotFoundError, OSError) as exc:
                self.close()
                raise UinputUnavailableError(str(exc)) from exc

            log.info("virtual input devices created")

    def _create_absolute_pointer(self, UInput: Any, ecodes: Any, AbsInfo: Any) -> Any:
        """Create the absolute pointing device.

        ``input_props`` is only accepted by python-evdev 1.4 and newer.  On
        older builds the device is still created, just without
        ``INPUT_PROP_POINTER``; libinput then has to infer the classification,
        which usually still works but is less reliable, so it is logged.
        """
        capabilities = {
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
            ecodes.EV_ABS: [
                (ecodes.ABS_X, _abs_axis(AbsInfo)),
                (ecodes.ABS_Y, _abs_axis(AbsInfo)),
            ],
        }
        common = {
            "name": f"{VIRTUAL_DEVICE_PREFIX} Pointer",
            "vendor": _VENDOR,
            "product": _PRODUCT + 2,
            "version": _VERSION,
            "bustype": ecodes.BUS_VIRTUAL,
        }
        try:
            return UInput(capabilities, input_props=[ecodes.INPUT_PROP_POINTER], **common)
        except TypeError:
            log.warning(
                "python-evdev is too old to set INPUT_PROP_POINTER; absolute "
                "pointer positioning may be less reliable on some compositors"
            )
            return UInput(capabilities, **common)

    def close(self) -> None:
        """Release everything held, then destroy the virtual devices."""
        with self._lock:
            if self.is_open:
                try:
                    self.release_all()
                except Exception:  # noqa: BLE001 - closing must not raise
                    log.exception("failed to release held keys while closing")
            for attr in ("_keyboard", "_mouse", "_pointer"):
                device = getattr(self, attr)
                if device is not None:
                    try:
                        device.close()
                    except Exception:  # noqa: BLE001
                        log.exception("failed to close %s", attr)
                    setattr(self, attr, None)
            self._held.clear()

    def __enter__(self) -> UinputSink:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- Configuration --------------------------------------------------

    def set_screen_size(self, width: int, height: int) -> None:
        """Tell the sink the desktop size, so pixel coordinates can be scaled.

        The daemon has no display connection, so the interface reports this when
        it connects and whenever the monitor layout changes.
        """
        if width > 0 and height > 0:
            with self._lock:
                self._screen = (int(width), int(height))

    @property
    def screen_size(self) -> tuple[int, int]:
        """The desktop size currently used for absolute coordinate scaling."""
        return self._screen

    # --- Emission -------------------------------------------------------

    def _require_open(self) -> None:
        if not self.is_open:
            raise UinputUnavailableError("virtual devices are not open")

    def key(self, code: str, pressed: bool) -> None:
        """Press or release a key or mouse button by kernel symbol.

        Unknown symbols are logged and ignored rather than raising, so one bad
        step cannot abort a macro mid-run and leave keys held.
        """
        with self._lock:
            self._require_open()
            try:
                numeric = keys.code_for(code)
            except KeyError:
                log.warning("ignoring unknown input code %r", code)
                return

            device = self._mouse if keys.is_mouse_button(code) else self._keyboard
            device.write(self._ecodes.EV_KEY, numeric, 1 if pressed else 0)
            device.syn()

            if pressed:
                self._held.add(code)
            else:
                self._held.discard(code)

    def move_absolute(self, x: int, y: int) -> None:
        """Move the pointer to a pixel position, clamped to the desktop."""
        with self._lock:
            self._require_open()
            width, height = self._screen
            abs_x = _scale(x, width)
            abs_y = _scale(y, height)
            self._pointer.write(self._ecodes.EV_ABS, self._ecodes.ABS_X, abs_x)
            self._pointer.write(self._ecodes.EV_ABS, self._ecodes.ABS_Y, abs_y)
            self._pointer.syn()

    def move_relative(self, dx: int, dy: int) -> None:
        """Move the pointer by a pixel offset.

        Note that the compositor applies pointer acceleration to relative
        motion, so the pointer will not necessarily travel exactly *dx* pixels.
        Use :meth:`move_absolute` when precision matters.
        """
        with self._lock:
            self._require_open()
            if dx:
                self._mouse.write(self._ecodes.EV_REL, self._ecodes.REL_X, int(dx))
            if dy:
                self._mouse.write(self._ecodes.EV_REL, self._ecodes.REL_Y, int(dy))
            if dx or dy:
                self._mouse.syn()

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        """Turn the scroll wheel by *amount* detents."""
        with self._lock:
            self._require_open()
            if not amount:
                return
            axis = self._ecodes.REL_HWHEEL if horizontal else self._ecodes.REL_WHEEL
            self._mouse.write(self._ecodes.EV_REL, axis, int(amount))
            self._mouse.syn()

    def release_all(self) -> None:
        """Release every key and button this sink is holding down."""
        with self._lock:
            if not self.is_open:
                self._held.clear()
                return
            for code in sorted(self._held):
                try:
                    numeric = keys.code_for(code)
                except KeyError:
                    continue
                device = self._mouse if keys.is_mouse_button(code) else self._keyboard
                device.write(self._ecodes.EV_KEY, numeric, 0)
            if self._held:
                self._keyboard.syn()
                self._mouse.syn()
                log.debug("released %d held codes", len(self._held))
            self._held.clear()

    def held_codes(self) -> Iterator[str]:
        """The kernel symbols currently held down by this sink."""
        with self._lock:
            return iter(sorted(self._held))


def _abs_axis(AbsInfo: Any) -> Any:  # noqa: N803 - matches python-evdev's own name
    """Describe one axis of the absolute pointer's logical grid."""
    return AbsInfo(value=0, min=0, max=ABS_RANGE, fuzz=0, flat=0, resolution=0)


def _codes_with_prefix(prefix: str, maximum: int) -> list[int]:
    """Numeric codes for every generated symbol starting with *prefix*.

    ``KEY_RESERVED`` is excluded: advertising code 0 makes some tools report the
    device as having a bogus capability.
    """
    return sorted(
        {
            code
            for name, code in keys.CODES.items()
            if name.startswith(prefix) and 0 < code <= maximum
        }
    )


def _scale(pixel: int, extent: int) -> int:
    """Map a pixel coordinate onto the absolute device's logical grid."""
    if extent <= 1:
        return 0
    clamped = max(0, min(int(pixel), extent - 1))
    return round(clamped * ABS_RANGE / (extent - 1))
