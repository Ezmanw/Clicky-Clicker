"""Functional settings shared between the user interface and the daemon.

These live in ``settings.json`` rather than GSettings because the daemon reads
them too, and the daemon must not depend on a dconf session bus being present.
Purely presentational state -- colour scheme, window geometry -- stays in
GSettings, where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Settings", "DEFAULT_EMERGENCY_STOP"]

#: Held together, these stop every running macro and release any keys the
#: daemon is holding down.  Chosen because no desktop binds it by default and
#: it is reachable one-handed.  This combination is never suppressed and is
#: always active while the daemon runs -- it is the guaranteed way out of a
#: runaway macro even if the application window is not focused.
DEFAULT_EMERGENCY_STOP: list[str] = ["KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_ESC"]


def _as_bool(value: Any, fallback: bool) -> bool:
    return bool(value) if isinstance(value, bool) else fallback


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(slots=True)
class Settings:
    """Application settings that affect behaviour rather than appearance."""

    enabled: bool = True
    """Master switch.  When off the daemon stays running but installs nothing,
    which is a faster and more reliable way to get out of the way than stopping
    and restarting the service."""

    emergency_stop: list[str] = field(default_factory=lambda: list(DEFAULT_EMERGENCY_STOP))

    emergency_stop_enabled: bool = True

    autostart: bool = True
    """Whether the daemon's systemd user service is enabled for login."""

    notify_on_macro_start: bool = False

    default_gap_ms: int = 10
    """Seed value for a newly created macro's repeat gap."""

    recording_capture_delays: bool = True
    """Whether the recorder inserts Wait actions reflecting real timing."""

    recording_min_delay_ms: int = 1
    """Delays shorter than this are dropped during recording, which keeps
    recordings readable without meaningfully changing their timing."""

    excluded_devices: list[str] = field(default_factory=list)
    """Device identifiers the daemon must never open.

    The daemon already refuses to open its own virtual devices; this is for
    hardware the user wants left alone, such as a tablet or a game controller.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "enabled": self.enabled,
            "emergency_stop": list(self.emergency_stop),
            "emergency_stop_enabled": self.emergency_stop_enabled,
            "autostart": self.autostart,
            "notify_on_macro_start": self.notify_on_macro_start,
            "default_gap_ms": self.default_gap_ms,
            "recording_capture_delays": self.recording_capture_delays,
            "recording_min_delay_ms": self.recording_min_delay_ms,
            "excluded_devices": list(self.excluded_devices),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Settings:
        """Rebuild from disk, falling back to defaults for anything missing."""
        settings = cls()
        if not isinstance(data, dict):
            return settings

        settings.enabled = _as_bool(data.get("enabled"), settings.enabled)
        settings.emergency_stop_enabled = _as_bool(
            data.get("emergency_stop_enabled"), settings.emergency_stop_enabled
        )
        settings.autostart = _as_bool(data.get("autostart"), settings.autostart)
        settings.notify_on_macro_start = _as_bool(
            data.get("notify_on_macro_start"), settings.notify_on_macro_start
        )
        settings.recording_capture_delays = _as_bool(
            data.get("recording_capture_delays"), settings.recording_capture_delays
        )
        settings.default_gap_ms = max(
            0, _as_int(data.get("default_gap_ms"), settings.default_gap_ms)
        )
        settings.recording_min_delay_ms = max(
            0, _as_int(data.get("recording_min_delay_ms"), settings.recording_min_delay_ms)
        )

        stop = data.get("emergency_stop")
        if isinstance(stop, list) and stop:
            settings.emergency_stop = [str(k) for k in stop]

        excluded = data.get("excluded_devices")
        if isinstance(excluded, list):
            settings.excluded_devices = [str(d) for d in excluded]

        return settings
