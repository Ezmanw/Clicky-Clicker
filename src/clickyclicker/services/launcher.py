"""Opening desktop applications from the daemon.

The daemon can start at boot, before any graphical session exists, so its own
environment cannot be trusted to contain ``WAYLAND_DISPLAY`` and friends -- an
app started with it would simply fail to appear.  Apps are therefore started
with ``systemd-run --user``: a transient unit receives the user manager's
*current* environment, which the desktop fills in at login.

That also gives each app its own unit.  Spawned directly, the app would live in
the daemon's cgroup and be killed whenever the daemon restarted.

Deliberately free of GLib: the ``.desktop`` file is located and its ``Exec``
line expanded here, following the Desktop Entry Specification, so the daemon
keeps its no-GTK, no-gi footprint.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from ..models.app import AppRef

log = logging.getLogger(__name__)

__all__ = ["LaunchError", "exec_argv", "find_desktop_file", "launch"]

#: Field codes that expand to file/URL arguments or deprecated values. Opening
#: an app from a button passes none of those, so they are dropped.
_DROPPED_FIELD_CODES = {
    "%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m", "%i", "%c", "%k",
}
_EMBEDDED_FIELD_CODE = re.compile(r"%[a-zA-Z]")


class LaunchError(Exception):
    """An application could not be found or started."""


def _data_dirs() -> list[Path]:
    """Where ``applications/`` directories may live, most specific first."""
    home = Path.home()
    data_home = os.environ.get("XDG_DATA_HOME", "").strip() or str(home / ".local" / "share")
    system = os.environ.get("XDG_DATA_DIRS", "").strip() or "/usr/local/share:/usr/share"
    candidates = [Path(data_home)]
    candidates += [Path(entry) for entry in system.split(":") if entry.strip()]
    # The daemon may have started before the session added these to
    # XDG_DATA_DIRS, so Flatpak and Snap exports are always searched.
    candidates += [
        home / ".local" / "share" / "flatpak" / "exports" / "share",
        Path("/var/lib/flatpak/exports/share"),
        Path("/var/lib/snapd/desktop"),
        Path("/usr/local/share"),
        Path("/usr/share"),
    ]
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def find_desktop_file(app: AppRef) -> Path | None:
    """Locate the ``.desktop`` file for *app*, or ``None`` if it is gone."""
    if app.path and Path(app.path).is_file():
        return Path(app.path)

    # A desktop-file id maps "-" back to a subdirectory separator, so
    # "kde-org.foo.desktop" may live at applications/kde/org.foo.desktop.
    relative = [Path(app.id)]
    parts = app.id.split("-")
    for split in range(1, len(parts)):
        relative.append(Path(*parts[:split], "-".join(parts[split:])))

    for base in _data_dirs():
        for rel in relative:
            candidate = base / "applications" / rel
            if candidate.is_file():
                return candidate
    return None


def _unescape(value: str) -> str:
    """Undo the string-level escapes of a desktop entry value (\\s, \\n, ...)."""
    out: list[str] = []
    chars = iter(value)
    for char in chars:
        if char != "\\":
            out.append(char)
            continue
        nxt = next(chars, "")
        out.append({"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(nxt, "\\" + nxt))
    return "".join(out)


def _read_entry(path: Path) -> dict[str, str]:
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (configparser.Error, OSError) as exc:
        raise LaunchError(f"{path.name} could not be read: {exc}") from exc
    if not parser.has_section("Desktop Entry"):
        raise LaunchError(f"{path.name} has no [Desktop Entry] section")
    return dict(parser.items("Desktop Entry"))


def exec_argv(path: Path) -> tuple[list[str], dict[str, str]]:
    """The command line to run for a ``.desktop`` file, and its entry keys.

    :raises LaunchError: if the file has no usable ``Exec`` line.
    """
    entry = _read_entry(path)
    raw = entry.get("Exec", "").strip()
    if not raw:
        raise LaunchError(f"{path.name} does not say how to start the application")

    try:
        tokens = shlex.split(_unescape(raw))
    except ValueError as exc:
        raise LaunchError(f"{path.name} has a malformed Exec line: {exc}") from exc

    argv: list[str] = []
    for token in tokens:
        if token in _DROPPED_FIELD_CODES:
            continue
        cleaned = _EMBEDDED_FIELD_CODE.sub("", token.replace("%%", "\0")).replace("\0", "%")
        if cleaned:
            argv.append(cleaned)
    if not argv:
        raise LaunchError(f"{path.name} has an empty Exec line")
    return argv, entry


def _resolve(app: AppRef) -> tuple[list[str], dict[str, str]]:
    path = find_desktop_file(app)
    if path is None:
        raise LaunchError(f"{app.label} is no longer installed")
    argv, entry = exec_argv(path)

    program = argv[0]
    runnable = Path(program).is_absolute() and os.access(program, os.X_OK)
    if not runnable and shutil.which(program) is None:
        raise LaunchError(f"{app.label} cannot be started: {program} was not found")
    if entry.get("Terminal", "").lower() == "true":
        log.warning("%s is a terminal application and will run without a terminal", app.label)
    return argv, entry


#: Variables describing the *daemon's* own service invocation. Passed on, they
#: would make the app believe it is part of the daemon's unit -- writing its log
#: to the daemon's journal stream, for instance.
_SERVICE_ONLY_VARS = ("INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "MANAGERPID",
                      "SYSTEMD_EXEC_PID", "MAINPID", "WATCHDOG_PID", "WATCHDOG_USEC")

#: How long to watch a freshly started app for an immediate failure.
_STARTUP_GRACE = 3.0


def _decode_systemd_value(value: str) -> str:
    """Decode ``$'...'`` C-style quoting, which systemd uses for unusual values."""
    if not (value.startswith("$'") and value.endswith("'")):
        return value
    inner = value[2:-1]
    return inner.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def session_environment() -> dict[str, str]:
    """The environment a desktop-launched app should get.

    Read from the user service manager at the moment of launching, not taken
    from this process. The daemon can start at boot, before the desktop has told
    the manager about the session, so its own environment may lack
    ``WAYLAND_DISPLAY`` for its whole lifetime; the manager's is updated at
    login. Falls back to this process's environment if the manager is
    unreachable.
    """
    env: dict[str, str] = {}
    systemctl = shutil.which("systemctl")
    if systemctl is not None:
        try:
            result = subprocess.run(  # noqa: S603 - fixed binary, fixed arguments
                [systemctl, "--user", "show-environment"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            for line in result.stdout.splitlines():
                name, sep, value = line.partition("=")
                if not sep or not name:
                    continue
                try:
                    env[name] = _decode_systemd_value(value)
                except UnicodeError:
                    continue
    if not env:
        env = dict(os.environ)
    for name in _SERVICE_ONLY_VARS:
        env.pop(name, None)
    return env


def _start(app: AppRef, argv: list[str], entry: dict[str, str]) -> subprocess.Popen[bytes]:
    """Start *app* and return its process.

    Runs inside a transient systemd *scope* rather than a service. Many apps
    (COSMIC's among them) fork the real application into the background and
    exit at once; for a service, the main process exiting stops the unit and
    kills the forked app with it, so the window flashes up and vanishes. A
    scope lives as long as any process in it does. It also keeps the app out of
    the daemon's own cgroup, so restarting the daemon never closes your apps.
    """
    workdir = entry.get("Path", "").strip() or str(Path.home())
    cwd = workdir if Path(workdir).is_dir() else str(Path.home())
    env = session_environment()

    command = argv
    systemd_run = shutil.which("systemd-run")
    if systemd_run is not None:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", app.id.removesuffix(".desktop")).strip("-")[:40]
        command = [
            systemd_run,
            "--user",
            "--scope",
            "--collect",
            "--quiet",
            f"--unit=app-clickyclicker-{slug or 'app'}-{uuid.uuid4().hex[:8]}",
            f"--description={app.label}",
            "--",
            *argv,
        ]

    return subprocess.Popen(  # noqa: S603 - argv comes from an installed .desktop file
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def launch(app: AppRef, on_error: Callable[[str], None] | None = None) -> None:
    """Open *app* in the background.  Returns immediately.

    Runs on its own thread so a slow start never delays the input reader that
    triggered it. The thread watches briefly for an immediate failure, then
    stays to reap the process when the app eventually exits. Failures are
    logged and passed to *on_error* as a sentence suitable for showing to the
    user.
    """

    def report(message: str) -> None:
        log.warning("%s", message)
        if on_error is not None:
            on_error(message)

    def run() -> None:
        try:
            argv, entry = _resolve(app)
            process = _start(app, argv, entry)
        except LaunchError as exc:
            report(str(exc))
            return
        except OSError as exc:
            report(f"{app.label} could not be started: {exc}")
            return

        try:
            code = process.wait(timeout=_STARTUP_GRACE)
        except subprocess.TimeoutExpired:
            log.info("opened %s", app.label)
            process.wait()
            return
        # Exiting straight away with success is normal for apps that hand off
        # to an already-running instance, or that fork into the background.
        if code == 0:
            log.info("opened %s", app.label)
        else:
            report(f"{app.label} exited straight away (status {code})")

    threading.Thread(target=run, name=f"launch-{app.id}", daemon=True).start()
