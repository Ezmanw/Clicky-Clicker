"""Starting the daemon automatically when the user logs in.

Uses ``systemctl --user enable``, which is the mechanism the desktop already
provides: systemd starts the unit as part of the session, restarts it if it
fails, and stops it at logout.  Writing a private launcher into
``~/.config/autostart`` would duplicate all of that and supervise nothing.

A ``.desktop`` autostart file is used only as a fallback on systems without a
systemd user instance, so the feature still works on those rather than being
silently unavailable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ..persistence import paths
from .daemon_client import SERVICE_NAME

log = logging.getLogger(__name__)

__all__ = ["is_systemd_available", "is_enabled", "set_enabled", "status_detail"]

_AUTOSTART_FILE = "clicky-clicker-daemon.desktop"

_FALLBACK_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Clicky Clicker Input Service
Comment=Applies Clicky Clicker's input mappings and macros
Exec=clicky-clicker-daemon
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    binary = shutil.which("systemctl")
    if binary is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed binary, fixed arguments
            [binary, "--user", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("systemctl %s failed: %s", " ".join(args), exc)
        return None


def is_systemd_available() -> bool:
    """Whether a systemd user instance is running and knows about the unit."""
    if shutil.which("systemctl") is None:
        return False
    result = _systemctl("show", SERVICE_NAME, "--property=LoadState", "--value")
    if result is None or result.returncode != 0:
        return False
    return result.stdout.strip() in {"loaded", "stub"}


def _autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".config"
    return root / "autostart" / _AUTOSTART_FILE


# --- Query ----------------------------------------------------------------


def is_enabled() -> bool:
    """Whether the daemon is set to start at login, by either mechanism."""
    if is_systemd_available():
        result = _systemctl("is-enabled", SERVICE_NAME)
        if result is not None and result.stdout.strip() == "enabled":
            return True
    return _autostart_path().exists()


def status_detail() -> str:
    """A short description of how autostart is configured, for the subtitle."""
    if is_systemd_available():
        result = _systemctl("is-enabled", SERVICE_NAME)
        state = result.stdout.strip() if result is not None else "unknown"
        if state == "enabled":
            return "Managed by systemd as a user service."
        return "Managed by systemd. Not currently enabled."
    if _autostart_path().exists():
        return "Managed by a desktop autostart entry."
    return "systemd is unavailable; a desktop autostart entry will be used."


# --- Change ---------------------------------------------------------------


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Turn login autostart on or off.

    :returns: ``(succeeded, message)``.  The message is shown to the user, so it
        explains what actually happened rather than just reporting a code.
    """
    if is_systemd_available():
        return _set_via_systemd(enabled)
    return _set_via_desktop_file(enabled)


def _set_via_systemd(enabled: bool) -> tuple[bool, str]:
    verb = "enable" if enabled else "disable"
    # --now also starts or stops it, so the setting takes effect immediately
    # rather than only after the next login.
    result = _systemctl(verb, "--now", SERVICE_NAME)
    if result is None:
        return False, "systemctl is not available."
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1] if detail else f"systemctl {verb} failed."
    return True, (
        "The input service will start automatically at login."
        if enabled
        else "The input service will no longer start at login."
    )


def _set_via_desktop_file(enabled: bool) -> tuple[bool, str]:
    path = _autostart_path()
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_FALLBACK_DESKTOP_ENTRY, encoding="utf-8")
            return True, "A desktop autostart entry was created."
        path.unlink(missing_ok=True)
        return True, "The desktop autostart entry was removed."
    except OSError as exc:
        return False, f"Could not update {path}: {exc}"


def daemon_command() -> str:
    """The command the service runs, shown in the troubleshooting dialog."""
    binary = shutil.which("clicky-clicker-daemon")
    return binary or "clicky-clicker-daemon"


def config_location() -> Path:
    """Where the user's macros and settings live, shown in preferences."""
    return paths.config_dir()
