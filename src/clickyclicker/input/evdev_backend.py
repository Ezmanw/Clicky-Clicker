"""Reading physical input through the kernel's evdev interface.

This is the half of the design that Wayland cannot provide: a Wayland client
only receives events for its own focused surfaces, so global hotkeys are
impossible above the display server.  Reading ``/dev/input/event*`` sits below
it and therefore works on every compositor, at the cost of needing membership
of the ``input`` group.

Suppressing an input
--------------------
Withholding an event from the rest of the session requires an exclusive grab
(``EVIOCGRAB``) on the whole device -- the kernel has no way to grab a single
code.  A grab takes *everything*, so grabbing a mouse merely to swallow its
side button would also swallow pointer motion and leave the user unable to move
the cursor.

Each grabbed device therefore gets a companion virtual device that mirrors its
capabilities, and every event except the suppressed ones is forwarded through
it verbatim.  Devices with nothing to suppress are opened without a grab and
simply observed, which is both cheaper and safer.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import selectors
import threading
import time
from collections.abc import Callable
from typing import Any

from ..models import keys
from .backend import DeviceInfo, InputEvent, InputSource, KeyState
from .errors import BackendUnavailableError, PermissionDeniedError
from .uinput_sink import VIRTUAL_DEVICE_PREFIX

log = logging.getLogger(__name__)

__all__ = ["EvdevSource", "SUPPRESS_ANY_DEVICE"]

#: Key of the wildcard entry in the suppression map: applies to every device.
SUPPRESS_ANY_DEVICE = "*"

#: How often the listen loop rescans for added or removed devices.  Polling
#: rather than watching netlink keeps the daemon free of a pyudev dependency;
#: three seconds is imperceptible for plugging in a keyboard.
_RESCAN_INTERVAL = 3.0

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _load_evdev() -> Any:
    """Import python-evdev, translating absence into a typed error."""
    try:
        import evdev  # noqa: PLC0415 - optional dependency, probed at runtime
    except ImportError as exc:  # pragma: no cover - depends on host packages
        raise BackendUnavailableError(str(exc)) from exc
    return evdev


def device_identifier(device: Any) -> str:
    """A stable id for a device.

    Deliberately not the ``/dev/input/eventN`` path, which is assigned in
    discovery order and changes across reboots and replugs; a binding restricted
    to "this keyboard" must survive both.
    """
    slug = _SLUG_RE.sub("-", (device.name or "device").lower()).strip("-")
    ident = f"{device.info.vendor:04x}:{device.info.product:04x}:{slug}"
    uniq = getattr(device, "uniq", "") or ""
    return f"{ident}:{uniq}" if uniq else ident


class EvdevSource(InputSource):
    """Watches every readable input device for key and button transitions."""

    def __init__(self, excluded_devices: set[str] | None = None) -> None:
        self._evdev = _load_evdev()
        self._excluded = set(excluded_devices or ())
        self._lock = threading.RLock()
        self._suppressed: dict[str, set[str]] = {}
        self._running = False
        self._wake_r = -1
        self._wake_w = -1
        self._open: dict[str, _OpenDevice] = {}
        self._needs_reconfigure = False
        self._warned_zero_devices = False

    # --- Enumeration ----------------------------------------------------

    def list_devices(self) -> list[DeviceInfo]:
        """Enumerate readable devices, excluding this application's own.

        :raises PermissionDeniedError: if no device can be opened at all, which
            almost always means the user is not in the ``input`` group.
        """
        evdev = self._evdev
        try:
            paths = evdev.list_devices()
        except PermissionError as exc:
            raise PermissionDeniedError(str(exc)) from exc

        found: list[DeviceInfo] = []
        denied = 0
        for path in sorted(paths):
            try:
                device = evdev.InputDevice(path)
            except PermissionError:
                denied += 1
                continue
            except OSError:
                continue
            try:
                if self._is_own_device(device):
                    continue
                info = self._describe(device)
                if info is not None:
                    found.append(info)
            finally:
                device.close()

        if not found and denied:
            raise PermissionDeniedError(
                f"{denied} input device(s) could not be opened for reading"
            )
        return found

    def _is_own_device(self, device: Any) -> bool:
        """Whether *device* is one we created ourselves.

        Reading our own virtual devices would feed injected events straight back
        into the engine, so a single macro keypress would retrigger the macro.
        """
        return (device.name or "").startswith(VIRTUAL_DEVICE_PREFIX)

    def _describe(self, device: Any) -> DeviceInfo | None:
        """Build a :class:`DeviceInfo`, or ``None`` if the device is irrelevant."""
        capabilities = device.capabilities()
        key_codes = set(capabilities.get(self._evdev.ecodes.EV_KEY, ()))
        if not key_codes:
            return None

        has_mouse = bool(key_codes & {self._evdev.ecodes.BTN_LEFT, self._evdev.ecodes.BTN_RIGHT})
        has_keyboard = any(
            code in key_codes
            for code in (
                self._evdev.ecodes.KEY_A,
                self._evdev.ecodes.KEY_Z,
                self._evdev.ecodes.KEY_SPACE,
            )
        )
        return DeviceInfo(
            id=device_identifier(device),
            name=device.name or device.path,
            path=device.path,
            has_keyboard=has_keyboard,
            has_mouse=has_mouse,
            vendor=device.info.vendor,
            product=device.info.product,
        )

    # --- Suppression ----------------------------------------------------

    def set_suppressed(self, codes: dict[str, set[str] | None]) -> None:
        """Declare which codes to withhold, per device id or ``"*"``.

        Takes effect on the next pass of the listen loop, which is woken
        immediately, so this is safe to call from any thread.
        """
        with self._lock:
            self._suppressed = {
                device_id: set(entries)
                for device_id, entries in codes.items()
                if entries
            }
            self._needs_reconfigure = True
        self._wake()

    def _suppressed_for(self, device_id: str) -> set[str]:
        with self._lock:
            return self._suppressed.get(SUPPRESS_ANY_DEVICE, set()) | self._suppressed.get(
                device_id, set()
            )

    # --- Listening ------------------------------------------------------

    def listen(self, on_event: Callable[[InputEvent], None]) -> None:
        """Read events until :meth:`stop` is called.  Blocks the caller."""
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        self._running = True

        selector = selectors.DefaultSelector()
        selector.register(self._wake_r, selectors.EVENT_READ, None)
        last_scan = 0.0

        try:
            while self._running:
                now = time.monotonic()
                with self._lock:
                    reconfigure = self._needs_reconfigure
                    self._needs_reconfigure = False
                if reconfigure or now - last_scan >= _RESCAN_INTERVAL:
                    self._sync_devices(selector)
                    last_scan = now

                for key, _mask in selector.select(timeout=_RESCAN_INTERVAL):
                    if key.data is None:
                        _drain(self._wake_r)
                        continue
                    self._pump(key.data, on_event, selector)
        finally:
            self._teardown(selector)

    def stop(self) -> None:
        """Ask :meth:`listen` to return.  Safe to call from another thread."""
        self._running = False
        self._wake()

    def _wake(self) -> None:
        if self._wake_w >= 0:
            with contextlib.suppress(OSError):
                os.write(self._wake_w, b"\x01")

    # --- Device management ----------------------------------------------

    def _sync_devices(self, selector: selectors.BaseSelector) -> None:
        """Open newly-arrived devices, drop departed ones, refresh grabs."""
        evdev = self._evdev
        try:
            paths = set(evdev.list_devices())
        except (PermissionError, OSError) as exc:
            log.warning("cannot enumerate input devices: %s", exc)
            return

        for path in sorted(paths - set(self._open)):
            self._open_device(path, selector)

        for path in list(set(self._open) - paths):
            self._drop_device(path, selector)

        # Grab state depends on the suppression map, which can change at any
        # time; re-evaluate every device on each pass.
        for open_device in list(self._open.values()):
            wanted = self._suppressed_for(open_device.info.id)
            if wanted != open_device.suppressed:
                self._apply_grab(open_device, wanted)

        self._warn_if_nothing_readable(paths)

    def _warn_if_nothing_readable(self, paths: set[str]) -> None:
        """Log once if devices exist but none could be opened.

        This is the situation a user hits after running ``usermod -aG input``
        without fully logging out: the daemon (started by ``systemd --user``
        before the group change) keeps the credentials it started with, so
        every device open silently fails and every binding does nothing, with
        no error anywhere. Restarting just this service does not help, because
        it is re-forked by the same still-stale ``systemd --user`` manager --
        the fix is a full log out and back in (or a reboot).
        """
        if self._open or not paths or self._warned_zero_devices:
            return
        self._warned_zero_devices = True
        log.warning(
            "%d input device(s) exist but none could be opened for reading. "
            "If you just added this account to the 'input' group, that change "
            "has not taken effect for this service yet -- log out and back in "
            "(or reboot), then restart clicky-clicker-daemon.service. "
            "Restarting the service alone will not fix this, since it is "
            "relaunched by the same systemd user session that is still stale.",
            len(paths),
        )

    @property
    def watched_device_count(self) -> int:
        """How many devices are currently open for reading.

        Distinct from :meth:`list_devices`, which the interface calls from its
        own (usually freshly-started) process and can therefore report success
        even while the daemon itself -- a different, possibly longer-lived
        process -- reads nothing at all.

        Safe to read from another thread without a lock: ``self._open`` is only
        ever mutated by the listener thread, and ``len()`` on a dict is an
        atomic read of its size field in CPython.
        """
        return len(self._open)

    def _open_device(self, path: str, selector: selectors.BaseSelector) -> None:
        try:
            device = self._evdev.InputDevice(path)
        except (PermissionError, OSError):
            return

        try:
            if self._is_own_device(device):
                device.close()
                return
            info = self._describe(device)
            if info is None or info.id in self._excluded:
                device.close()
                return
        except OSError:
            device.close()
            return

        open_device = _OpenDevice(device=device, info=info)
        self._open[path] = open_device
        selector.register(device.fileno(), selectors.EVENT_READ, open_device)
        self._apply_grab(open_device, self._suppressed_for(info.id))
        log.debug("watching %s (%s)", info.name, path)

    def _drop_device(self, path: str, selector: selectors.BaseSelector) -> None:
        open_device = self._open.pop(path, None)
        if open_device is None:
            return
        with contextlib.suppress(KeyError, OSError, ValueError):
            selector.unregister(open_device.device.fileno())
        open_device.release(self._evdev)
        log.debug("stopped watching %s", path)

    def _apply_grab(self, open_device: _OpenDevice, wanted: set[str]) -> None:
        """Take or drop the exclusive grab needed to suppress *wanted*."""
        if wanted and not open_device.grabbed:
            try:
                open_device.start_forwarding(self._evdev)
                open_device.device.grab()
                open_device.grabbed = True
            except OSError as exc:
                open_device.stop_forwarding()
                log.warning(
                    "cannot suppress inputs on %s: %s", open_device.info.name, exc
                )
                # Leave `suppressed` empty so events still reach the engine;
                # the mapping works, it just does not hide the original.
                open_device.suppressed = set()
                return
        elif not wanted and open_device.grabbed:
            with contextlib.suppress(OSError):
                open_device.device.ungrab()
            open_device.grabbed = False
            open_device.stop_forwarding()

        open_device.suppressed = set(wanted)

    # --- Event pump -----------------------------------------------------

    def _pump(
        self,
        open_device: _OpenDevice,
        on_event: Callable[[InputEvent], None],
        selector: selectors.BaseSelector,
    ) -> None:
        """Drain one device's pending events."""
        ecodes = self._evdev.ecodes
        try:
            for event in open_device.device.read():
                if event.type == ecodes.EV_KEY:
                    name = keys.name_for_code(event.code)
                    suppressed = name is not None and name in open_device.suppressed
                    if not suppressed and open_device.grabbed:
                        open_device.forward(event)
                    if name is not None and event.value in (0, 1, 2):
                        on_event(
                            InputEvent(
                                code=name,
                                state=KeyState(event.value),
                                timestamp=time.monotonic(),
                                device_id=open_device.info.id,
                            )
                        )
                elif open_device.grabbed:
                    # Motion, scrolling and synchronisation must keep flowing or
                    # a grabbed mouse would stop working entirely.
                    open_device.forward(event)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.EBADF):
                self._drop_device(open_device.device.path, selector)
            else:
                log.warning("read error on %s: %s", open_device.info.name, exc)

    def _teardown(self, selector: selectors.BaseSelector) -> None:
        for path in list(self._open):
            self._drop_device(path, selector)
        selector.close()
        for fd in (self._wake_r, self._wake_w):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self._wake_r = self._wake_w = -1


class _OpenDevice:
    """A device the source currently holds open, plus its forwarding state."""

    __slots__ = ("device", "info", "grabbed", "suppressed", "_forward")

    def __init__(self, device: Any, info: DeviceInfo) -> None:
        self.device = device
        self.info = info
        self.grabbed = False
        self.suppressed: set[str] = set()
        self._forward: Any = None

    def start_forwarding(self, evdev: Any) -> None:
        """Create the companion device that replays non-suppressed events.

        :raises OSError: if ``/dev/uinput`` is unusable, which the caller treats
            as "cannot suppress on this device" rather than a fatal error.
        """
        if self._forward is not None:
            return
        self._forward = evdev.UInput.from_device(
            self.device,
            name=f"{VIRTUAL_DEVICE_PREFIX} Forward {self.info.name}"[:79],
        )

    def stop_forwarding(self) -> None:
        if self._forward is None:
            return
        try:
            self._forward.close()
        except Exception:  # noqa: BLE001 - teardown must not raise
            log.exception("failed to close forwarding device")
        self._forward = None

    def forward(self, event: Any) -> None:
        """Replay one event verbatim onto the companion device."""
        if self._forward is None:
            return
        try:
            self._forward.write(event.type, event.code, event.value)
        except OSError as exc:
            log.warning("failed to forward event from %s: %s", self.info.name, exc)

    def release(self, evdev: Any) -> None:
        """Ungrab, stop forwarding and close."""
        del evdev
        if self.grabbed:
            with contextlib.suppress(OSError):
                self.device.ungrab()
            self.grabbed = False
        self.stop_forwarding()
        with contextlib.suppress(OSError):
            self.device.close()


def _drain(fd: int) -> None:
    """Empty the wake-up pipe."""
    try:
        while os.read(fd, 4096):
            pass
    except BlockingIOError:
        pass
    except OSError:
        pass
