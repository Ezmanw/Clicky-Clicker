"""Input reading and injection.

Nothing here imports GTK.  The daemon depends on this package and on
:mod:`clickyclicker.models` only, so it runs headless in a systemd user service
with no display connection.
"""

from .backend import DeviceInfo, InputEvent, InputSink, InputSource, KeyState
from .devices import Capability, SystemReport, probe
from .errors import (
    BackendUnavailableError,
    DeviceOpenError,
    InputError,
    PermissionDeniedError,
    UinputUnavailableError,
)

__all__ = [
    "BackendUnavailableError",
    "Capability",
    "DeviceInfo",
    "DeviceOpenError",
    "InputError",
    "InputEvent",
    "InputSink",
    "InputSource",
    "KeyState",
    "PermissionDeniedError",
    "SystemReport",
    "UinputUnavailableError",
    "create_sink",
    "create_source",
    "probe",
]


def create_source(excluded_devices: set[str] | None = None) -> InputSource:
    """Build the input source for this system.

    Only evdev is implemented today; the indirection exists so the daemon and
    the recorder never name a backend directly.

    :raises BackendUnavailableError: if python-evdev is not installed.
    """
    from .evdev_backend import EvdevSource  # noqa: PLC0415 - keeps evdev optional

    return EvdevSource(excluded_devices=excluded_devices)


def create_sink() -> InputSink:
    """Build the input sink for this system.

    :raises BackendUnavailableError: if python-evdev is not installed.
    """
    from .uinput_sink import UinputSink  # noqa: PLC0415 - keeps evdev optional

    return UinputSink()
