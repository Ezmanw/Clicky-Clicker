"""The macro library: the single source of truth the interface binds to.

Everything the interface shows -- macros, bindings, settings -- lives here, and
every change goes through here.  Pages never touch the store directly, so there
is exactly one place that decides when something is written to disk and when the
daemon is told to reload.

Deliberately free of GTK.  Observers are plain callables rather than GObject
signals, which keeps the service layer testable without a display and keeps the
dependency arrow pointing one way: the interface knows about the library, the
library knows nothing about the interface.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from ..models import Binding, BindingKind, BindingSet, Macro, Settings
from ..persistence import MacroStore
from .daemon_client import DaemonClient

log = logging.getLogger(__name__)

__all__ = ["MacroLibrary"]

Observer = Callable[[], None]


class MacroLibrary:
    """Owns the loaded macros, bindings and settings, and persists changes."""

    def __init__(self, store: MacroStore | None = None, client: DaemonClient | None = None) -> None:
        self._store = store or MacroStore()
        self._client = client or DaemonClient()
        self._macros: list[Macro] = []
        self._bindings = BindingSet()
        self._settings = Settings()
        self._macro_observers: list[Observer] = []
        self._binding_observers: list[Observer] = []
        self._settings_observers: list[Observer] = []
        self.load_problems: list[str] = []
        """Human-readable descriptions of files skipped during loading."""

    # --- Loading --------------------------------------------------------

    def load(self, *, seed_examples: bool = True) -> None:
        """Read everything from disk.

        :param seed_examples: install the bundled example presets when the
            library is empty, so a new user has something to look at rather than
            an empty list.
        """
        if seed_examples:
            self._store.seed_examples_if_empty()

        self._macros = self._store.load_macros()
        self._bindings = self._store.load_bindings()
        self._settings = self._store.load_settings()
        self.load_problems = [problem.summary for problem in self._store.problems]

        self._emit_macros()
        self._emit_bindings()
        self._emit_settings()

    # --- Observers ------------------------------------------------------

    def connect_macros_changed(self, observer: Observer) -> None:
        self._macro_observers.append(observer)

    def connect_bindings_changed(self, observer: Observer) -> None:
        self._binding_observers.append(observer)

    def connect_settings_changed(self, observer: Observer) -> None:
        self._settings_observers.append(observer)

    def _emit(self, observers: Iterable[Observer]) -> None:
        for observer in list(observers):
            try:
                observer()
            except Exception:  # noqa: BLE001 - one bad observer must not block the rest
                log.exception("library observer failed")

    def _emit_macros(self) -> None:
        self._emit(self._macro_observers)

    def _emit_bindings(self) -> None:
        self._emit(self._binding_observers)

    def _emit_settings(self) -> None:
        self._emit(self._settings_observers)

    # --- Access ---------------------------------------------------------

    @property
    def macros(self) -> list[Macro]:
        """All macros, sorted by name."""
        return list(self._macros)

    @property
    def bindings(self) -> list[Binding]:
        return list(self._bindings.bindings)

    @property
    def binding_set(self) -> BindingSet:
        return self._bindings

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def daemon(self) -> DaemonClient:
        return self._client

    def macro(self, macro_id: str | None) -> Macro | None:
        """Look up a macro by id."""
        if not macro_id:
            return None
        return next((m for m in self._macros if m.id == macro_id), None)

    def macro_name(self, macro_id: str | None) -> str | None:
        macro = self.macro(macro_id)
        return macro.name if macro else None

    def unique_name(self, base: str) -> str:
        """A name not already taken, by appending a number if needed."""
        existing = {macro.name for macro in self._macros}
        if base not in existing:
            return base
        index = 2
        while f"{base} {index}" in existing:
            index += 1
        return f"{base} {index}"

    def bindings_for(self, macro_id: str) -> list[Binding]:
        """Every binding that runs the given macro."""
        return self._bindings.for_macro(macro_id)

    # --- Macro commands -------------------------------------------------

    def create_macro(self, name: str = "New Macro") -> Macro:
        """Create, save and return a new empty macro."""
        macro = Macro(name=self.unique_name(name))
        macro.playback.gap_ms = self._settings.default_gap_ms
        self._macros.append(macro)
        self._macros.sort(key=lambda m: m.name.casefold())
        self.save_macro(macro)
        return macro

    def add_macro(self, macro: Macro) -> Macro:
        """Add an already-built macro, e.g. one that was just imported."""
        macro.name = self.unique_name(macro.name)
        self._macros.append(macro)
        self._macros.sort(key=lambda m: m.name.casefold())
        self.save_macro(macro)
        return macro

    def save_macro(self, macro: Macro) -> None:
        """Persist a macro and tell the daemon to pick it up.

        :raises OSError: if the file cannot be written.
        """
        self._store.save_macro(macro)
        self._macros.sort(key=lambda m: m.name.casefold())
        self._emit_macros()
        self._notify_daemon()

    def duplicate_macro(self, macro: Macro) -> Macro:
        """Copy a macro under a new name."""
        copy = macro.duplicate(self.unique_name(f"{macro.name} (Copy)"))
        return self.add_macro(copy)

    def rename_macro(self, macro: Macro, name: str) -> None:
        """Rename a macro, keeping names unique.

        :raises OSError: if the file cannot be written.
        """
        cleaned = name.strip()
        if not cleaned or cleaned == macro.name:
            return
        others = {m.name for m in self._macros if m is not macro}
        macro.name = cleaned if cleaned not in others else self.unique_name(cleaned)
        self.save_macro(macro)

    def delete_macro(self, macro: Macro) -> list[Binding]:
        """Delete a macro and return the bindings that pointed at it.

        The bindings are kept rather than removed, and reported as broken on the
        mappings page, so the user can repoint them instead of silently losing
        the trigger they had set up.
        """
        orphaned = self.bindings_for(macro.id)
        self._store.delete_macro(macro.id)
        self._macros = [m for m in self._macros if m.id != macro.id]
        self._emit_macros()
        if orphaned:
            self._emit_bindings()
        self._notify_daemon()
        return orphaned

    # --- Import and export ----------------------------------------------

    def import_macro(self, path: Path) -> Macro:
        """Import a preset file into the library.

        :raises ValueError: if the file is not a valid preset.
        :raises OSError: if it cannot be read or the copy cannot be written.
        """
        macro = self._store.import_macro(path)
        return self.add_macro(macro)

    def export_macro(self, macro: Macro, path: Path) -> None:
        """Write a macro out as a shareable preset file.

        :raises OSError: if the destination cannot be written.
        """
        self._store.export_macro(macro, path)

    # --- Binding commands -----------------------------------------------

    def add_binding(self, binding: Binding) -> Binding:
        """Add and persist a binding."""
        self._bindings.bindings.append(binding)
        self.save_bindings()
        return binding

    def remove_binding(self, binding: Binding) -> None:
        """Delete a binding."""
        self._bindings.bindings = [b for b in self._bindings.bindings if b.id != binding.id]
        self.save_bindings()

    def duplicate_binding(self, binding: Binding) -> Binding:
        return self.add_binding(binding.duplicate())

    def save_bindings(self) -> None:
        """Persist all bindings and tell the daemon to reload.

        :raises OSError: if the file cannot be written.
        """
        self._store.save_bindings(self._bindings)
        self._emit_bindings()
        self._notify_daemon()

    def broken_bindings(self) -> list[Binding]:
        """Bindings whose macro no longer exists, or which are incomplete."""
        known = {macro.id for macro in self._macros}
        return [
            binding
            for binding in self._bindings.bindings
            if (binding.kind is BindingKind.RUN_MACRO and binding.macro_id not in known)
            or (binding.kind is BindingKind.REMAP and not binding.output)
        ]

    # --- Settings commands ----------------------------------------------

    def save_settings(self) -> None:
        """Persist settings and tell the daemon to reload.

        :raises OSError: if the file cannot be written.
        """
        self._store.save_settings(self._settings)
        self._emit_settings()
        self._notify_daemon()

    def set_enabled(self, enabled: bool) -> None:
        """Flip the master switch."""
        if self._settings.enabled == enabled:
            return
        self._settings.enabled = enabled
        self.save_settings()

    # --- Daemon ---------------------------------------------------------

    def _notify_daemon(self) -> None:
        """Ask the daemon to re-read the configuration.

        Failure is expected and harmless when the daemon is not running -- the
        interface already shows that state in a banner -- so it is only logged.
        """
        if not self._client.reload():
            log.debug("daemon did not acknowledge reload; it may not be running")
