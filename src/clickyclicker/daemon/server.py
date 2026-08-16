"""The daemon's control socket.

A small threaded Unix socket server.  Each connection is handled on its own
thread; connections are short-lived and infrequent (the interface polls once a
second at most), so a thread per client is simpler and cheaper here than an
event loop, and it keeps the daemon free of any GLib dependency.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any

from ..services.ipc import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, Command, decode, encode, error, ok
from .engine import Engine

log = logging.getLogger(__name__)

__all__ = ["ControlServer"]

_BACKLOG = 8
_CLIENT_TIMEOUT = 15.0


class ControlServer:
    """Accepts control connections and dispatches them onto an :class:`Engine`."""

    def __init__(self, engine: Engine, socket_path: Path) -> None:
        self._engine = engine
        self._path = socket_path
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.on_shutdown_requested: "threading.Event" = threading.Event()

    # --- Lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Bind the socket and start accepting in the background.

        :raises OSError: if the socket cannot be created or bound.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._clear_stale_socket()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Restrict the socket before it is reachable: it accepts commands
            # that synthesise input, so it must never be world-writable, not
            # even briefly.
            old_umask = os.umask(0o177)
            try:
                server.bind(str(self._path))
            finally:
                os.umask(old_umask)
            os.chmod(self._path, 0o600)
            server.listen(_BACKLOG)
        except OSError:
            server.close()
            raise

        self._server = server
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="control", daemon=True)
        self._thread.start()
        log.info("listening on %s", self._path)

    def _clear_stale_socket(self) -> None:
        """Remove a socket left behind by a previous run.

        Only if nothing is listening on it: if a daemon really is running, the
        connect succeeds and this raises rather than stealing its socket.

        :raises OSError: if another daemon already holds the socket.
        """
        if not self._path.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(str(self._path))
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            self._path.unlink(missing_ok=True)
            return
        finally:
            probe.close()
        raise OSError(f"another Clicky Clicker daemon is already listening on {self._path}")

    def stop(self) -> None:
        """Stop accepting and remove the socket."""
        self._running = False
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            server.close()
        self._path.unlink(missing_ok=True)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # --- Accept loop ----------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running:
            server = self._server
            if server is None:
                break
            try:
                connection, _ = server.accept()
            except OSError:
                if self._running:
                    log.debug("accept failed while running", exc_info=True)
                break
            threading.Thread(
                target=self._serve_client, args=(connection,), name="control-client", daemon=True
            ).start()

    def _serve_client(self, connection: socket.socket) -> None:
        connection.settimeout(_CLIENT_TIMEOUT)
        try:
            with connection, connection.makefile("rwb") as stream:
                for raw in stream:
                    if len(raw) > MAX_MESSAGE_BYTES:
                        stream.write(encode(error("message too large")))
                        stream.flush()
                        return
                    try:
                        request = decode(raw.rstrip(b"\n"))
                    except ValueError as exc:
                        stream.write(encode(error(str(exc))))
                        stream.flush()
                        continue
                    stream.write(encode(self._dispatch(request)))
                    stream.flush()
        except (OSError, socket.timeout):
            return

    # --- Dispatch -------------------------------------------------------

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle one request.  Never raises: failures become error replies."""
        raw_command = request.get("command")
        try:
            command = Command(raw_command)
        except ValueError:
            return error(f"unknown command {raw_command!r}")

        try:
            return self._handle(command, request)
        except Exception as exc:  # noqa: BLE001 - a bad request must not kill the daemon
            log.exception("command %s failed", command.value)
            return error(str(exc))

    def _handle(self, command: Command, request: dict[str, Any]) -> dict[str, Any]:
        if command is Command.PING:
            return ok(version=PROTOCOL_VERSION)

        if command is Command.STATUS:
            return ok(status=self._engine.status())

        if command is Command.RELOAD:
            self._engine.reload()
            return ok(status=self._engine.status())

        if command is Command.STOP_ALL:
            return ok(stopped=self._engine.stop_all_macros())

        if command is Command.RUN_MACRO:
            macro_id = request.get("macro_id")
            if not isinstance(macro_id, str) or not macro_id:
                return error("run_macro requires a macro_id")
            started = self._engine.run_macro(macro_id, once=bool(request.get("once", False)))
            return ok(started=started)

        if command is Command.SET_SCREEN:
            width = _positive_int(request.get("width"))
            height = _positive_int(request.get("height"))
            if width is None or height is None:
                return error("set_screen requires positive width and height")
            self._engine.set_screen_size(width, height)
            return ok()

        if command is Command.SHUTDOWN:
            self.on_shutdown_requested.set()
            return ok()

        return error(f"unhandled command {command.value!r}")  # pragma: no cover


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
