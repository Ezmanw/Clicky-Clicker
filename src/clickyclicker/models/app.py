"""A reference to an installed desktop application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["AppRef"]


@dataclass(frozen=True, slots=True)
class AppRef:
    """Which application to open, as chosen in the app picker.

    The desktop-file id is what identifies the app; the name is kept so the
    interface can describe a binding without looking the app up, and the path
    is a hint that saves a search when the file has not moved.
    """

    id: str
    """Desktop-file id, e.g. ``firefox.desktop`` or ``org.gnome.Calculator.desktop``."""

    name: str = ""
    path: str = ""

    @property
    def label(self) -> str:
        """Human-readable name, falling back to the id."""
        return self.name or self.id.removesuffix(".desktop")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "path": self.path}

    @classmethod
    def from_dict(cls, data: Any) -> AppRef | None:
        """Rebuild from disk, or ``None`` if *data* does not name an app."""
        if not isinstance(data, dict):
            return None
        app_id = data.get("id")
        if not isinstance(app_id, str) or not app_id.strip():
            return None
        return cls(
            id=app_id,
            name=str(data.get("name") or ""),
            path=str(data.get("path") or ""),
        )
