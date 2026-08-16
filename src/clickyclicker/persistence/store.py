"""Loading and saving macros, bindings and settings.

Every write goes through :func:`write_json`, which writes to a temporary file in
the destination directory and then renames it into place.  A crash or a full
disk therefore leaves the previous file intact rather than a truncated one --
worth the small amount of ceremony, because the alternative is a user losing a
macro library to an interrupted save.

Loading is forgiving by design: a single corrupt preset is reported and skipped,
not allowed to prevent the application from starting.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Binding, BindingSet, Macro, Settings
from . import paths

log = logging.getLogger(__name__)

__all__ = ["LoadProblem", "MacroStore", "read_json", "write_json"]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class LoadProblem:
    """A file that could not be loaded, described well enough to show a user."""

    path: Path
    reason: str

    @property
    def summary(self) -> str:
        return f"{self.path.name}: {self.reason}"


def read_json(path: Path) -> Any:
    """Read and parse a JSON file.

    :raises FileNotFoundError: if the file does not exist.
    :raises ValueError: if it is not valid JSON or not valid UTF-8.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not valid UTF-8 text ({exc.reason})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON (line {exc.lineno}: {exc.msg})") from exc


def write_json(path: Path, data: Any) -> None:
    """Write *data* as formatted JSON, atomically.

    :raises OSError: if the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class MacroStore:
    """The user's macro library, bindings and settings on disk."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self.problems: list[LoadProblem] = []
        """Files skipped during the most recent load, for the interface to report."""

    # --- Paths ----------------------------------------------------------

    @property
    def macros_path(self) -> Path:
        return self._root / "macros" if self._root else paths.macros_dir()

    @property
    def bindings_path(self) -> Path:
        return self._root / "bindings.json" if self._root else paths.bindings_file()

    @property
    def settings_path(self) -> Path:
        return self._root / "settings.json" if self._root else paths.settings_file()

    def macro_path(self, macro_id: str) -> Path:
        """Path of one macro's file.

        The id is sanitised even though it is generated internally, so that a
        hand-edited or imported file can never direct a write outside the
        macros directory.
        """
        safe = _SAFE_NAME.sub("_", macro_id) or "macro"
        return self.macros_path / f"{safe}.json"

    # --- Macros ---------------------------------------------------------

    def load_macros(self) -> list[Macro]:
        """Load every saved macro, skipping and recording any that are broken."""
        self.problems = []
        directory = self.macros_path
        if not directory.is_dir():
            return []

        macros: list[Macro] = []
        for path in sorted(directory.glob("*.json")):
            try:
                macro = Macro.from_dict(read_json(path))
            except (ValueError, OSError) as exc:
                self.problems.append(LoadProblem(path=path, reason=str(exc)))
                log.warning("skipping unreadable macro %s: %s", path, exc)
                continue
            macros.append(macro)

        macros.sort(key=lambda m: m.name.casefold())
        return macros

    def save_macro(self, macro: Macro) -> None:
        """Write one macro to disk.

        :raises OSError: if the file cannot be written.
        """
        write_json(self.macro_path(macro.id), macro.to_dict())

    def delete_macro(self, macro_id: str) -> None:
        """Remove a macro's file.  Missing files are not an error."""
        self.macro_path(macro_id).unlink(missing_ok=True)

    def export_macro(self, macro: Macro, destination: Path) -> None:
        """Write a macro to an arbitrary location, for sharing.

        :raises OSError: if the destination cannot be written.
        """
        write_json(destination, macro.to_dict())

    def import_macro(self, source: Path) -> Macro:
        """Read a macro from an arbitrary location and give it a fresh identity.

        A new id is assigned so importing a preset that originated from this
        machine cannot silently overwrite the original.

        :raises ValueError: if the file is not a valid macro.
        :raises OSError: if it cannot be read.
        """
        macro = Macro.from_dict(read_json(source), new_id=True)
        if not macro.actions:
            raise ValueError("the preset contains no actions")
        return macro

    def load_example_presets(self) -> list[Macro]:
        """Load the bundled example presets from the first directory that has them."""
        for directory in paths.system_preset_dirs():
            if not directory.is_dir():
                continue
            examples: list[Macro] = []
            for path in sorted(directory.glob("*.json")):
                try:
                    examples.append(Macro.from_dict(read_json(path), new_id=True))
                except (ValueError, OSError) as exc:
                    log.warning("skipping bundled preset %s: %s", path, exc)
            if examples:
                return examples
        return []

    # --- Bindings -------------------------------------------------------

    def load_bindings(self) -> BindingSet:
        """Load the binding set, returning an empty one if absent or broken."""
        try:
            return BindingSet.from_dict(read_json(self.bindings_path))
        except FileNotFoundError:
            return BindingSet()
        except (ValueError, OSError) as exc:
            self.problems.append(LoadProblem(path=self.bindings_path, reason=str(exc)))
            log.warning("could not read bindings: %s", exc)
            return BindingSet()

    def save_bindings(self, bindings: BindingSet) -> None:
        """Write the binding set.

        :raises OSError: if the file cannot be written.
        """
        write_json(self.bindings_path, bindings.to_dict())

    # --- Settings -------------------------------------------------------

    def load_settings(self) -> Settings:
        """Load settings, falling back to defaults if absent or broken."""
        try:
            return Settings.from_dict(read_json(self.settings_path))
        except FileNotFoundError:
            return Settings()
        except (ValueError, OSError) as exc:
            self.problems.append(LoadProblem(path=self.settings_path, reason=str(exc)))
            log.warning("could not read settings: %s", exc)
            return Settings()

    def save_settings(self, settings: Settings) -> None:
        """Write settings.

        :raises OSError: if the file cannot be written.
        """
        write_json(self.settings_path, settings.to_dict())

    # --- Bulk -----------------------------------------------------------

    def seed_examples_if_empty(self) -> list[Macro]:
        """Install the bundled presets on first run.

        Returns the macros that were installed, or an empty list if the library
        already had content.  Only ever runs when the library is genuinely
        empty, so it cannot resurrect presets the user deleted.
        """
        directory = self.macros_path
        if directory.is_dir() and any(directory.glob("*.json")):
            return []

        examples = self.load_example_presets()
        for macro in examples:
            try:
                self.save_macro(macro)
            except OSError as exc:
                log.warning("could not install example preset %s: %s", macro.name, exc)
                return []
        if examples:
            log.info("installed %d example presets", len(examples))
        return examples


def unused_binding_cleanup(bindings: BindingSet, macro_ids: set[str]) -> list[Binding]:
    """Bindings that point at a macro which no longer exists.

    Returned rather than deleted: the mappings page shows them as broken so the
    user can repoint them, which is friendlier than silently discarding work.
    """
    from ..models import BindingKind  # noqa: PLC0415 - avoids a cycle at import time

    return [
        binding
        for binding in bindings.bindings
        if binding.kind is BindingKind.RUN_MACRO
        and (binding.macro_id is None or binding.macro_id not in macro_ids)
    ]
