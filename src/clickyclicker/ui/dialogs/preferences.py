"""Application preferences.

Built on ``AdwPreferencesDialog``, which is the current form of the preferences
window described by the GNOME HIG (``AdwPreferencesWindow`` is its deprecated
predecessor and is not used in new code).

Colour scheme is handled by ``AdwStyleManager`` rather than by any custom
styling: the application ships no theme of its own and simply asks libadwaita to
prefer light or dark.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gio, Gtk

from ...models.keys import format_combo
from ...models.settings import DEFAULT_EMERGENCY_STOP
from ...services import MacroLibrary, autostart
from .combo_editor import edit_combo

__all__ = ["PreferencesDialog", "apply_color_scheme", "COLOR_SCHEMES"]

#: Stored value to :class:`Adw.ColorScheme`, in the order shown in the combo.
COLOR_SCHEMES: list[tuple[str, str, Adw.ColorScheme]] = [
    ("default", "Follow the system", Adw.ColorScheme.DEFAULT),
    ("light", "Light", Adw.ColorScheme.FORCE_LIGHT),
    ("dark", "Dark", Adw.ColorScheme.FORCE_DARK),
]


def apply_color_scheme(value: str) -> None:
    """Apply a stored colour-scheme preference through ``AdwStyleManager``."""
    manager = Adw.StyleManager.get_default()
    for stored, _label, scheme in COLOR_SCHEMES:
        if stored == value:
            manager.set_color_scheme(scheme)
            return
    manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


class PreferencesDialog(Adw.PreferencesDialog):
    """Application-level settings."""

    def __init__(
        self,
        library: MacroLibrary,
        gsettings: Gio.Settings | None,
        *,
        on_toast: Callable[..., None],
    ) -> None:
        super().__init__()
        self._library = library
        self._gsettings = gsettings
        self._on_toast = on_toast
        self._updating = False

        self.set_title("Preferences")
        self.add(self._build_general_page())
        self.add(self._build_safety_page())
        self._render()

    # --- Pages ----------------------------------------------------------

    def _build_general_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")

        appearance = Adw.PreferencesGroup(title="Appearance")
        model = Gtk.StringList()
        for _value, label, _scheme in COLOR_SCHEMES:
            model.append(label)
        self._scheme_row = Adw.ComboRow(title="Colour Scheme", model=model)
        self._scheme_row.connect("notify::selected", self._on_scheme_changed)
        appearance.add(self._scheme_row)
        page.add(appearance)

        startup = Adw.PreferencesGroup(
            title="Startup",
            description=(
                "The background service applies your mappings. Starting it at "
                "login means they work as soon as you sign in."
            ),
        )
        self._autostart_row = Adw.SwitchRow(title="Start Automatically at Login")
        self._autostart_row.connect("notify::active", self._on_autostart_changed)
        startup.add(self._autostart_row)

        self._enabled_row = Adw.SwitchRow(
            title="Apply Mappings",
            subtitle="Turn off to suspend every mapping without stopping the service",
        )
        self._enabled_row.connect("notify::active", self._on_enabled_changed)
        startup.add(self._enabled_row)
        page.add(startup)

        defaults = Adw.PreferencesGroup(title="Defaults for New Macros")
        self._gap_row = Adw.SpinRow(
            title="Repeat Gap",
            subtitle="Milliseconds between repeats of a new macro",
            adjustment=Gtk.Adjustment(lower=0, upper=60_000, step_increment=1, page_increment=10),
        )
        self._gap_row.connect("notify::value", self._on_gap_changed)
        defaults.add(self._gap_row)
        page.add(defaults)

        recording = Adw.PreferencesGroup(title="Recording")
        self._capture_delays_row = Adw.SwitchRow(
            title="Capture Timing",
            subtitle="Insert Wait actions reflecting how fast you performed the macro",
        )
        self._capture_delays_row.connect("notify::active", self._on_capture_delays_changed)
        recording.add(self._capture_delays_row)

        self._min_delay_row = Adw.SpinRow(
            title="Shortest Captured Delay",
            subtitle="Gaps below this are left out, keeping recordings readable",
            adjustment=Gtk.Adjustment(lower=0, upper=1000, step_increment=1, page_increment=10),
        )
        self._min_delay_row.connect("notify::value", self._on_min_delay_changed)
        recording.add(self._min_delay_row)
        page.add(recording)

        location = Adw.PreferencesGroup(title="Files")
        location_row = Adw.ActionRow(
            title="Configuration Folder", subtitle=str(autostart.config_location())
        )
        location_row.set_subtitle_lines(0)
        location_row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        open_button = Gtk.Button(icon_name="folder-open-symbolic")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.set_tooltip_text("Open the configuration folder")
        open_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Open the configuration folder"]
        )
        open_button.connect("clicked", self._on_open_folder)
        location_row.add_suffix(open_button)
        location.add(location_row)
        page.add(location)

        return page

    def _build_safety_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Safety", icon_name="dialog-warning-symbolic")

        group = Adw.PreferencesGroup(
            title="Emergency Stop",
            description=(
                "Held together, these keys stop every running macro and release "
                "anything being held down. This works even when the window is "
                "closed, and is never intercepted by a mapping."
            ),
        )

        self._emergency_enabled_row = Adw.SwitchRow(title="Enable Emergency Stop")
        self._emergency_enabled_row.connect("notify::active", self._on_emergency_enabled_changed)
        group.add(self._emergency_enabled_row)

        self._emergency_row = Adw.ActionRow(title="Shortcut")
        self._emergency_row.add_prefix(Gtk.Image(icon_name="input-keyboard-symbolic"))
        change = Gtk.Button(label="Change")
        change.set_valign(Gtk.Align.CENTER)
        change.connect("clicked", lambda *_: self._on_change_emergency())
        self._emergency_row.add_suffix(change)
        self._emergency_row.set_activatable_widget(change)
        group.add(self._emergency_row)

        reset = Gtk.Button(label="Reset to Default")
        reset.set_halign(Gtk.Align.CENTER)
        reset.connect("clicked", lambda *_: self._on_reset_emergency())
        group.add(reset)
        page.add(group)

        return page

    # --- Rendering ------------------------------------------------------

    def _render(self) -> None:
        self._updating = True
        try:
            settings = self._library.settings

            stored = self._gsettings.get_string("color-scheme") if self._gsettings else "default"
            values = [value for value, _label, _scheme in COLOR_SCHEMES]
            self._scheme_row.set_selected(values.index(stored) if stored in values else 0)

            self._autostart_row.set_active(autostart.is_enabled())
            self._autostart_row.set_subtitle(autostart.status_detail())

            self._enabled_row.set_active(settings.enabled)
            self._gap_row.set_value(settings.default_gap_ms)
            self._capture_delays_row.set_active(settings.recording_capture_delays)
            self._min_delay_row.set_value(settings.recording_min_delay_ms)
            self._min_delay_row.set_sensitive(settings.recording_capture_delays)

            self._emergency_enabled_row.set_active(settings.emergency_stop_enabled)
            self._emergency_row.set_subtitle(format_combo(settings.emergency_stop))
            self._emergency_row.set_sensitive(settings.emergency_stop_enabled)
        finally:
            self._updating = False

    def _save(self) -> None:
        try:
            self._library.save_settings()
        except OSError as exc:
            self._on_toast(f"Could not save preferences: {exc}")

    # --- Handlers -------------------------------------------------------

    def _on_scheme_changed(self, combo: Adw.ComboRow, _param: object) -> None:
        if self._updating:
            return
        value = COLOR_SCHEMES[combo.get_selected()][0]
        apply_color_scheme(value)
        if self._gsettings is not None:
            self._gsettings.set_string("color-scheme", value)

    def _on_autostart_changed(self, switch: Adw.SwitchRow, _param: object) -> None:
        if self._updating:
            return
        succeeded, message = autostart.set_enabled(switch.get_active())
        self._on_toast(message)
        self._library.settings.autostart = switch.get_active() and succeeded
        self._save()
        self._render()

    def _on_enabled_changed(self, switch: Adw.SwitchRow, _param: object) -> None:
        if self._updating:
            return
        self._library.set_enabled(switch.get_active())

    def _on_gap_changed(self, spin: Adw.SpinRow, _param: object) -> None:
        if self._updating:
            return
        self._library.settings.default_gap_ms = int(spin.get_value())
        self._save()

    def _on_capture_delays_changed(self, switch: Adw.SwitchRow, _param: object) -> None:
        if self._updating:
            return
        self._library.settings.recording_capture_delays = switch.get_active()
        self._min_delay_row.set_sensitive(switch.get_active())
        self._save()

    def _on_min_delay_changed(self, spin: Adw.SpinRow, _param: object) -> None:
        if self._updating:
            return
        self._library.settings.recording_min_delay_ms = int(spin.get_value())
        self._save()

    def _on_emergency_enabled_changed(self, switch: Adw.SwitchRow, _param: object) -> None:
        if self._updating:
            return
        self._library.settings.emergency_stop_enabled = switch.get_active()
        self._save()
        self._render()

    def _on_change_emergency(self) -> None:
        def changed(codes: list[str]) -> None:
            if not codes:
                self._on_toast("The emergency stop needs at least one key")
                return
            self._library.settings.emergency_stop = list(codes)
            self._save()
            self._render()

        edit_combo(self, list(self._library.settings.emergency_stop), changed)

    def _on_reset_emergency(self) -> None:
        self._library.settings.emergency_stop = list(DEFAULT_EMERGENCY_STOP)
        self._save()
        self._render()
        self._on_toast("Emergency stop reset to the default")

    def _on_open_folder(self, _button: Gtk.Button) -> None:
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(str(autostart.config_location())))
        launcher.launch(self.get_root(), None, None)
