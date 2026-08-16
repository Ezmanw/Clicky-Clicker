"""Environment probing, so the interface can explain problems before they bite.

The application depends on two kernel facilities that are commonly missing or
unreadable on a fresh install: ``/dev/input/event*`` for reading and
``/dev/uinput`` for injecting.  Rather than letting the first macro fail with an
errno, the interface probes up front and shows an actionable status page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Capability", "SystemReport", "probe"]

_INPUT_DIR = Path("/dev/input")
_UINPUT = Path("/dev/uinput")


@dataclass(frozen=True, slots=True)
class Capability:
    """The state of one prerequisite."""

    available: bool
    summary: str
    remedy: str = ""


@dataclass(frozen=True, slots=True)
class SystemReport:
    """The result of probing every prerequisite."""

    evdev_module: Capability
    device_read: Capability
    uinput_write: Capability

    @property
    def ready(self) -> bool:
        """Whether macros and mappings can actually run."""
        return (
            self.evdev_module.available
            and self.device_read.available
            and self.uinput_write.available
        )

    @property
    def problems(self) -> list[Capability]:
        """Every unmet prerequisite, in the order they should be fixed."""
        return [
            capability
            for capability in (self.evdev_module, self.device_read, self.uinput_write)
            if not capability.available
        ]


def _probe_evdev() -> Capability:
    try:
        import evdev  # noqa: F401, PLC0415 - probing for presence only
    except ImportError:
        return Capability(
            available=False,
            summary="The python-evdev bindings are not installed",
            remedy=(
                "Install “python3-evdev” on Debian, Ubuntu and Pop!_OS, or "
                "“python-evdev” on Fedora and Arch."
            ),
        )
    return Capability(available=True, summary="Input bindings are installed")


def _probe_device_read() -> Capability:
    if not _INPUT_DIR.is_dir():
        return Capability(
            available=False,
            summary="No input devices were found",
            remedy="/dev/input does not exist. This is unexpected on a running system.",
        )

    nodes = sorted(_INPUT_DIR.glob("event*"))
    readable = [node for node in nodes if os.access(node, os.R_OK)]
    if not nodes:
        return Capability(
            available=False,
            summary="No input devices were found",
            remedy="No /dev/input/event* nodes exist.",
        )
    if not readable:
        return Capability(
            available=False,
            summary=f"None of the {len(nodes)} input devices can be read",
            remedy=(
                "Add your user to the “input” group, then log out and back in:\n"
                "sudo usermod -aG input $USER"
            ),
        )
    return Capability(
        available=True,
        summary=f"{len(readable)} of {len(nodes)} input devices are readable",
    )


def _probe_uinput() -> Capability:
    if not _UINPUT.exists():
        return Capability(
            available=False,
            summary="The uinput device is missing",
            remedy=(
                "Load the kernel module and make it load at boot:\n"
                "sudo modprobe uinput\n"
                'echo uinput | sudo tee /etc/modules-load.d/uinput.conf'
            ),
        )
    if not os.access(_UINPUT, os.W_OK):
        return Capability(
            available=False,
            summary="The uinput device is not writable",
            remedy=(
                "Install the bundled udev rule so the “input” group may write to it:\n"
                "sudo cp data/99-clicky-clicker-uinput.rules /etc/udev/rules.d/\n"
                "sudo udevadm control --reload-rules && sudo udevadm trigger"
            ),
        )
    return Capability(available=True, summary="Virtual input devices can be created")


def probe() -> SystemReport:
    """Check every prerequisite without opening any device exclusively."""
    return SystemReport(
        evdev_module=_probe_evdev(),
        device_read=_probe_device_read(),
        uinput_write=_probe_uinput(),
    )
