"""Application lifecycle and process entry points.

The GTK and libadwaita versions are pinned here, before anything imports
``gi.repository``.  This is the first module the launchers touch, and PyGObject
warns (and could bind the wrong version) if a namespace is imported without a
version having been requested first.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from .application import VERSION, ClickyClickerApplication, main  # noqa: E402

__all__ = ["VERSION", "ClickyClickerApplication", "main"]
