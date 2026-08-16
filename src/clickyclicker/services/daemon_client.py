"""Talking to the daemon from the interface.

Every call opens a connection, sends one request, reads one reply and closes.
That is slightly wasteful and entirely worth it: there is no persistent
connection to lose, no reconnect logic, and a daemon restart is invisible to the
interface.

All calls use short timeouts and never raise on connection failure -- a daemon
that is not running is a normal state the interface reports in a banner, not an
exception it has to catch everywhere.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..persistence import paths
from .ipc import Command, decode, encode

log = logging.getLogger(__name__)

__all__ = ["DaemonStatus", "DaemonClient", "SERVICE_NAME"]

#: Name of the systemd user unit installed by ``data/meson.build``.
SERVICE_NAME = "clicky-clicker-daemon.service"

_CONNECT_TIMEOUT = 1.0
_REPLY_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class DaemonStatus:
    """What the daemon reports about itself."""

    connected: bool
    enabled: bool = False
    macros: int = 0
    bindings: int = 0
    running: tuple[dict[str, str], ...] = field(default_factory=tuple)
    last_error: str = ""

    @property
    def running_count(self) -> int:
        return len(self.running)

    @property
    def running_names(self) -> list[str]:
        return [entry.get("name", "Macro") for entry in self.running]

    def summary(self) -> str:
        """One line for the window's status banner."""
        if not self.connected:
            return "The background service is not running, so mappings are inactive."
        if not self.enabled:
            return "Mappings are turned off."
        if self.running:
            names = ", ".join(self.running_names)
            return f"Running: {names}"
        return f"{self.bindings} mapping(s) active."


class DaemonClient:
    """A thin client for the daemon's control socket."""

    def __init__(self, socket_path: Path | None = None) -> None:
        self._path = socket_path or paths.socket_path()

    # --- Transport ------------------------------------------------------

    def _request(self, command: Command, **fields: Any) -> dict[str, Any] | None:
        """Send one command and return its reply, or ``None`` if unreachable."""
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(_CONNECT_TIMEOUT)
            connection.connect(str(self._path))
        except (OSError, socket.timeout):
            return None

        try:
            with connection, connection.makefile("rwb") as stream:
                connection.settimeout(_REPLY_TIMEOUT)
                stream.write(encode({"command": command.value, **fields}))
                stream.flush()
                line = stream.readline()
                if not line:
                    return None
                return decode(line.rstrip(b"\n"))
        except (OSError, socket.timeout, ValueError) as exc:
            log.debug("daemon request %s failed: %s", command.value, exc)
            return None

    # --- Commands -------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Whether the daemon answers.  Cheap enough to poll."""
        reply = self._request(Command.PING)
        return bool(reply and reply.get("ok"))

    def status(self) -> DaemonStatus:
        """Fetch the daemon's current state, or a disconnected placeholder."""
        reply = self._request(Command.STATUS)
        if not reply or not reply.get("ok"):
            return DaemonStatus(connected=False)

        payload = reply.get("status")
        if not isinstance(payload, dict):
            return DaemonStatus(connected=True)

        running = payload.get("running")
        entries = tuple(item for item in running if isinstance(item, dict)) if isinstance(running, list) else ()

        return DaemonStatus(
            connected=True,
            enabled=bool(payload.get("enabled", False)),
            macros=int(payload.get("macros", 0) or 0),
            bindings=int(payload.get("bindings", 0) or 0),
            running=entries,
            last_error=str(payload.get("last_error") or ""),
        )

    def reload(self) -> bool:
        """Ask the daemon to re-read the configuration.  Call after saving."""
        reply = self._request(Command.RELOAD)
        return bool(reply and reply.get("ok"))

    def stop_all(self) -> bool:
        """Stop every running macro."""
        reply = self._request(Command.STOP_ALL)
        return bool(reply and reply.get("ok"))

    def run_macro(self, macro_id: str, *, once: bool = True) -> bool:
        """Ask the daemon to play a macro, for the editor's Test command.

        Testing runs through the daemon rather than in-process so the test uses
        exactly the same execution path as a real trigger -- there is no second
        implementation that could behave differently.
        """
        reply = self._request(Command.RUN_MACRO, macro_id=macro_id, once=once)
        return bool(reply and reply.get("ok") and reply.get("started"))

    def set_screen_size(self, width: int, height: int) -> bool:
        """Report the desktop size so absolute coordinates land correctly."""
        reply = self._request(Command.SET_SCREEN, width=int(width), height=int(height))
        return bool(reply and reply.get("ok"))

    # --- Service management ---------------------------------------------

    @staticmethod
    def _systemctl(*args: str) -> subprocess.CompletedProcess[str] | None:
        """Run ``systemctl --user``, or return ``None`` if systemd is absent."""
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

    def start_service(self) -> bool:
        """Start the daemon's user service now."""
        result = self._systemctl("start", SERVICE_NAME)
        return result is not None and result.returncode == 0

    def stop_service(self) -> bool:
        """Stop the daemon's user service."""
        result = self._systemctl("stop", SERVICE_NAME)
        return result is not None and result.returncode == 0

    def restart_service(self) -> bool:
        """Restart the daemon's user service."""
        result = self._systemctl("restart", SERVICE_NAME)
        return result is not None and result.returncode == 0

    def service_error(self) -> str:
        """The last few journal lines, for the troubleshooting dialog."""
        binary = shutil.which("journalctl")
        if binary is None:
            return ""
        try:
            result = subprocess.run(  # noqa: S603 - fixed binary, fixed arguments
                [binary, "--user", "-u", SERVICE_NAME, "-n", "20", "--no-pager", "-o", "cat"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""
