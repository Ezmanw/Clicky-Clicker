"""System status: prerequisites, the background service, and detected devices.

This page exists because the most common failure for an application like this is
environmental rather than logical -- the user is not in the ``input`` group, or
``uinput`` is not loaded -- and those failures are invisible until a macro
silently does nothing.  Stating the situation plainly, with the exact command to
fix it, is more useful than any error dialog raised after the fact.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, GLib, Gtk

from ...input import probe
from ...input.errors import InputError
from ...services import MacroLibrary
from ...services.daemon_client import DaemonStatus
from ..widgets.rows import plain

__all__ = ["DevicesPage"]


class DevicesPage(Adw.NavigationPage):
    """Shows whether the application can actually do its job."""

    def __init__(self, library: MacroLibrary, *, on_toast: Callable[..., None]) -> None:
        super().__init__()
        self._library = library
        self._on_toast = on_toast

        self.set_title("Status")
        self.set_tag("status")

        self._requirements = Adw.PreferencesGroup(
            title="Requirements",
            description=(
                "Reading input devices and creating a virtual device both need "
                "permission from the system."
            ),
        )
        self._service_group = Adw.PreferencesGroup(
            title="Background Service",
            description=(
                "Mappings are applied by a background service, so they keep "
                "working when this window is closed."
            ),
        )
        self._devices_group = Adw.PreferencesGroup(title="Detected Devices")
        self._rows: list[tuple[Adw.PreferencesGroup, Gtk.Widget]] = []

        self._build()
        self.refresh()

    # --- Construction ---------------------------------------------------

    def _build(self) -> None:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Status"))

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Check again")
        refresh.update_property([Gtk.AccessibleProperty.LABEL], ["Check the system again"])
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)

        page = Adw.PreferencesPage()
        page.add(self._requirements)
        page.add(self._service_group)
        page.add(self._devices_group)

        scroller = Gtk.ScrolledWindow(child=page, vexpand=True)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

    # --- Rendering ------------------------------------------------------

    def refresh(self) -> None:
        """Re-probe the system and rebuild every section."""
        for group, row in self._rows:
            group.remove(row)
        self._rows = []

        self._render_requirements()
        self._render_service()
        self._render_devices()

    def _add(self, group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        group.add(row)
        self._rows.append((group, row))

    def _render_requirements(self) -> None:
        report = probe()
        for capability in (report.evdev_module, report.device_read, report.uinput_write):
            row = plain(Adw.ActionRow(), title=capability.summary)
            icon = Gtk.Image(
                icon_name=(
                    "emblem-ok-symbolic" if capability.available else "dialog-warning-symbolic"
                )
            )
            icon.add_css_class("success" if capability.available else "warning")
            row.add_prefix(icon)

            if not capability.available and capability.remedy:
                row.set_subtitle(capability.remedy)
                row.set_subtitle_lines(0)
                command = _first_command(capability.remedy)
                if command:
                    copy = Gtk.Button(icon_name="edit-copy-symbolic")
                    copy.set_valign(Gtk.Align.CENTER)
                    copy.set_tooltip_text("Copy the command")
                    copy.update_property(
                        [Gtk.AccessibleProperty.LABEL], ["Copy the command to the clipboard"]
                    )
                    copy.connect("clicked", self._on_copy, command)
                    row.add_suffix(copy)

            self._add(self._requirements, row)

    def _render_service(self) -> None:
        status = self._library.daemon.status()

        row = plain(Adw.ActionRow(title="Service"))
        icon = Gtk.Image(
            icon_name=(
                "emblem-ok-symbolic" if status.connected else "dialog-warning-symbolic"
            )
        )
        icon.add_css_class("success" if status.connected else "warning")
        row.add_prefix(icon)
        row.set_subtitle(status.summary())
        row.set_subtitle_lines(0)

        button = Gtk.Button(label="Restart" if status.connected else "Start")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", self._on_service_button, status)
        row.add_suffix(button)
        self._add(self._service_group, row)

        if status.connected and status.last_error:
            error_row = plain(Adw.ActionRow(), title="Last Error", subtitle=status.last_error)
            error_row.set_subtitle_lines(0)
            error_row.add_prefix(Gtk.Image(icon_name="dialog-error-symbolic"))
            self._add(self._service_group, error_row)

    def _render_devices(self) -> None:
        try:
            from ...input import create_source  # noqa: PLC0415 - optional dependency

            devices = create_source().list_devices()
        except InputError as exc:
            row = plain(Adw.ActionRow(), title=exc.title, subtitle=exc.remedy or exc.detail)
            row.set_subtitle_lines(0)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            self._add(self._devices_group, row)
            self._devices_group.set_description("")
            return
        except Exception as exc:  # noqa: BLE001 - never let this page fail to render
            row = plain(Adw.ActionRow(), title="Could Not List Devices", subtitle=str(exc))
            row.set_subtitle_lines(0)
            row.add_prefix(Gtk.Image(icon_name="dialog-error-symbolic"))
            self._add(self._devices_group, row)
            return

        self._devices_group.set_description(
            f"{len(devices)} device(s) can be read." if devices else ""
        )
        if not devices:
            row = Adw.ActionRow(
                title="No Readable Devices",
                subtitle="No keyboards or pointers could be opened for reading.",
            )
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            self._add(self._devices_group, row)
            return

        for device in devices:
            row = plain(Adw.ActionRow(), title=device.name, subtitle=device.kind_label)
            row.add_prefix(Gtk.Image(icon_name=device.icon_name))
            identifier = Gtk.Label(label=device.path)
            identifier.add_css_class("dim-label")
            identifier.add_css_class("caption")
            identifier.set_valign(Gtk.Align.CENTER)
            row.add_suffix(identifier)
            self._add(self._devices_group, row)

    # --- Commands -------------------------------------------------------

    def _on_copy(self, button: Gtk.Button, command: str) -> None:
        display = button.get_display() or Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(command)
            self._on_toast("Command copied to the clipboard")

    def _on_service_button(self, _button: Gtk.Button, status: DaemonStatus) -> None:
        client = self._library.daemon
        succeeded = client.restart_service() if status.connected else client.start_service()
        if not succeeded:
            self._on_toast("Could not control the service. Is systemd available?")
            return
        self._on_toast(
            "Restarting the service…" if status.connected else "Starting the service…"
        )
        # Give systemd a moment to bring it up before reporting the new state.
        GLib.timeout_add(1200, self._refresh_once)

    def _refresh_once(self) -> bool:
        self.refresh()
        return GLib.SOURCE_REMOVE


def _first_command(remedy: str) -> str:
    """The first shell command in a remedy, for the copy button."""
    for line in remedy.splitlines():
        stripped = line.strip()
        if stripped.startswith(("sudo ", "echo ", "systemctl ")):
            return stripped
    return ""
