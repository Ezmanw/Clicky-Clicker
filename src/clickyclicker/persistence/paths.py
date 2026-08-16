"""Filesystem locations, resolved from the XDG base directory specification.

Deliberately implemented with :mod:`os` rather than ``GLib.get_user_config_dir``
so that the daemon -- which has no display connection and no GTK dependency --
can share exactly the same paths as the interface.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "APP_ID",
    "APP_DIR_NAME",
    "config_dir",
    "macros_dir",
    "bindings_file",
    "settings_file",
    "runtime_dir",
    "socket_path",
    "state_dir",
    "log_file",
    "system_preset_dirs",
    "ensure_directories",
]

APP_ID = "io.github.ezmanw.ClickyClicker"
APP_DIR_NAME = "clicky-clicker"


def _xdg(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    return Path(value) if value else fallback


def config_dir() -> Path:
    """``~/.config/clicky-clicker`` — user macros, bindings and settings."""
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / APP_DIR_NAME


def macros_dir() -> Path:
    """Directory holding one JSON file per saved macro.

    One file per macro rather than a single database, because that makes a
    preset and a saved macro the same thing: exporting is a file copy.
    """
    return config_dir() / "macros"


def bindings_file() -> Path:
    """``bindings.json`` — which input runs which macro."""
    return config_dir() / "bindings.json"


def settings_file() -> Path:
    """``settings.json`` — behavioural settings shared with the daemon."""
    return config_dir() / "settings.json"


def state_dir() -> Path:
    """``~/.local/state/clicky-clicker`` — logs and other non-portable state."""
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_DIR_NAME


def log_file() -> Path:
    """Daemon log, used when not running under systemd's journal."""
    return state_dir() / "daemon.log"


def runtime_dir() -> Path:
    """Directory for the control socket.

    Falls back to a private directory under ``/tmp`` when ``XDG_RUNTIME_DIR`` is
    unset, which happens in minimal container environments.
    """
    base = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if base:
        return Path(base) / APP_DIR_NAME
    return Path(f"/tmp/{APP_DIR_NAME}-{os.getuid()}")  # noqa: S108 - mode 0700 below


def socket_path() -> Path:
    """Unix socket the interface uses to talk to the daemon."""
    return runtime_dir() / "daemon.sock"


def system_preset_dirs() -> list[Path]:
    """Read-only directories searched for the bundled example presets.

    Covers a Meson install prefix, the XDG data directories, and a source
    checkout, so the examples are found whether the application was installed
    or is being run from the build tree.
    """
    candidates: list[Path] = []

    # Set by the installed launchers to the exact Meson install prefix, which is
    # authoritative when the application was installed somewhere non-standard.
    installed = os.environ.get("CLICKY_CLICKER_PKGDATADIR", "").strip()
    if installed:
        candidates.append(Path(installed) / "presets")

    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for entry in data_dirs.split(":"):
        if entry.strip():
            candidates.append(Path(entry.strip()) / APP_DIR_NAME / "presets")

    # Running straight from a source checkout: src/clickyclicker/persistence
    # -> repository root -> data/presets
    candidates.append(Path(__file__).resolve().parents[3] / "data" / "presets")

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def ensure_directories() -> None:
    """Create the writable directories, with private permissions.

    The runtime directory is created mode 0700 because the control socket in it
    accepts commands that synthesise input.
    """
    macros_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
    runtime_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
