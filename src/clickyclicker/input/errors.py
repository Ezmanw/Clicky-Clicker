"""Typed failures from the input layer.

Each carries a ``title`` and ``detail`` suitable for showing directly in an
``AdwAlertDialog`` or an ``AdwStatusPage``, plus an optional ``remedy`` giving
the user something concrete to do.  The UI never has to interpret an errno.
"""

from __future__ import annotations

__all__ = [
    "InputError",
    "BackendUnavailableError",
    "PermissionDeniedError",
    "UinputUnavailableError",
    "DeviceOpenError",
]


class InputError(Exception):
    """Base class for every recoverable input-layer failure."""

    title = "Input Error"
    remedy = ""

    def __init__(self, detail: str = "", *, remedy: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        if remedy is not None:
            self.remedy = remedy


class BackendUnavailableError(InputError):
    """The evdev backend could not be loaded at all."""

    title = "Input Backend Unavailable"
    remedy = (
        "Install the python-evdev bindings — “python3-evdev” on Debian, Ubuntu "
        "and Pop!_OS, “python-evdev” on Fedora and Arch."
    )


class PermissionDeniedError(InputError):
    """The process cannot read ``/dev/input/event*``."""

    title = "Permission Denied"
    remedy = (
        "Add your user to the “input” group and log out and back in:\n"
        "sudo usermod -aG input $USER"
    )


class UinputUnavailableError(InputError):
    """``/dev/uinput`` is missing or not writable, so nothing can be sent."""

    title = "Cannot Create Virtual Device"
    remedy = (
        "Load the uinput module and install the bundled udev rule:\n"
        "sudo modprobe uinput\n"
        "sudo cp data/99-clicky-clicker-uinput.rules /etc/udev/rules.d/\n"
        "sudo udevadm control --reload-rules && sudo udevadm trigger"
    )


class DeviceOpenError(InputError):
    """A specific device could not be opened or grabbed.

    Not fatal: the daemon reports it, skips the device and carries on with the
    rest, because one unreadable device should not disable every mapping.
    """

    title = "Device Unavailable"

    def __init__(self, path: str, detail: str = "") -> None:
        super().__init__(detail)
        self.path = path
