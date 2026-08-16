"""Shared test fixtures.

Every test here runs without a display and without input hardware: the layers
under test import neither GTK nor evdev.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clickyclicker.input.backend import InputSink  # noqa: E402


class RecordingSink(InputSink):
    """An in-memory sink that records what a macro would have emitted."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._held: set[str] = set()

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def key(self, code: str, pressed: bool) -> None:
        self.events.append(("press" if pressed else "release", code))
        if pressed:
            self._held.add(code)
        else:
            self._held.discard(code)

    def move_absolute(self, x: int, y: int) -> None:
        self.events.append(("move_absolute", x, y))

    def move_relative(self, dx: int, dy: int) -> None:
        self.events.append(("move_relative", dx, dy))

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        self.events.append(("scroll", amount, horizontal))

    def release_all(self) -> None:
        for code in sorted(self._held):
            self.events.append(("release", code))
        self._held.clear()

    def held_codes(self):
        return iter(sorted(self._held))

    @property
    def held(self) -> set[str]:
        return set(self._held)


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A MacroStore rooted in a temporary directory."""
    from clickyclicker.persistence import MacroStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return MacroStore()
