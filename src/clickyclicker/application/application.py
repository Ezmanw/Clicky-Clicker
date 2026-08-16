"""The ``AdwApplication`` subclass and process entry point."""

from __future__ import annotations

import logging
import sys

from gi.repository import Adw, Gio, GLib, Gtk

from ..persistence import paths
from ..services import MacroLibrary
from ..ui.dialogs.preferences import apply_color_scheme
from ..ui.window import MainWindow

log = logging.getLogger(__name__)

__all__ = ["ClickyClickerApplication", "main"]

VERSION = "1.0.0"


class ClickyClickerApplication(Adw.Application):
    """The application."""

    def __init__(self) -> None:
        super().__init__(
            application_id=paths.APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._library = MacroLibrary()
        self._settings = _load_gsettings()
        self._window: MainWindow | None = None

        self._add_action("preferences", self._on_preferences, ["<Control>comma"])
        self._add_action("about", self._on_about)
        self._add_action("quit", self._on_quit, ["<Control>q"])

    def _add_action(
        self, name: str, handler: object, accelerators: list[str] | None = None
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda _a, _p: handler())  # type: ignore[operator]
        self.add_action(action)
        if accelerators:
            self.set_accels_for_action(f"app.{name}", accelerators)

    # --- Lifecycle ------------------------------------------------------

    def do_startup(self) -> None:
        """Prepare shared state before the first window appears."""
        Adw.Application.do_startup(self)

        try:
            paths.ensure_directories()
        except OSError as exc:
            log.warning("could not create the configuration directories: %s", exc)

        if self._settings is not None:
            apply_color_scheme(self._settings.get_string("color-scheme"))

        try:
            self._library.load()
        except Exception:  # noqa: BLE001 - an empty library beats a failed launch
            log.exception("could not load the macro library")

    def do_activate(self) -> None:
        """Show the window, creating it on first activation."""
        if self._window is None:
            self._window = MainWindow(self, self._library, self._settings)
        self._window.present()

    # --- Actions --------------------------------------------------------

    def _on_preferences(self) -> None:
        if self._window is not None:
            self._window.show_preferences()

    def _on_quit(self) -> None:
        # The daemon deliberately keeps running: mappings are meant to work
        # whether or not this window is open.
        self.quit()

    def _on_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="Clicky Clicker",
            application_icon="input-keyboard",
            version=VERSION,
            developer_name="Clicky Clicker contributors",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/Ezmanw/Clicky-Clicker",
            issue_url="https://github.com/Ezmanw/Clicky-Clicker/issues",
            comments=(
                "A Wayland-native input remapper and visual macro editor for "
                "GNOME, COSMIC and other modern Linux desktops."
            ),
        )
        about.add_credit_section(
            "Built With", ["GTK 4", "libadwaita", "python-evdev", "Meson"]
        )
        if self._window is not None:
            about.present(self._window)


def _load_gsettings() -> Gio.Settings | None:
    """Open the application's GSettings schema, if it is installed.

    Constructing ``Gio.Settings`` for a missing schema aborts the process, so
    the schema is looked up first.  Running from a source checkout without
    installing is a normal thing to do during development, and it should degrade
    to "window size is not remembered" rather than crashing.
    """
    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return None
    if source.lookup(paths.APP_ID, True) is None:
        log.info("GSettings schema %s is not installed; using defaults", paths.APP_ID)
        return None
    return Gio.Settings.new(paths.APP_ID)


def main(argv: list[str] | None = None) -> int:
    """Run the application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    GLib.set_application_name("Clicky Clicker")
    GLib.set_prgname(paths.APP_ID)

    application = ClickyClickerApplication()
    return application.run(argv if argv is not None else sys.argv)
