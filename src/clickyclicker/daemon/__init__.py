"""The background service that applies mappings when the window is closed.

Imports :mod:`clickyclicker.models`, :mod:`clickyclicker.input`,
:mod:`clickyclicker.macros` and :mod:`clickyclicker.persistence` -- never GTK.
"""

from .daemon import main
from .engine import Engine
from .server import ControlServer

__all__ = ["ControlServer", "Engine", "main"]
