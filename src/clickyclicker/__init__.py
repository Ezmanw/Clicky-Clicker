"""Clicky Clicker — a Wayland-native input remapper and visual macro editor.

Layering, from the bottom up.  Each layer may import the ones above it in this
list and never the ones below, which is what keeps the daemon runnable without a
display and the tests runnable without hardware::

    models        pure data; no I/O, no GTK, no evdev
    input         reading and injecting input; no GTK
    macros        playback, recording, validation; no GTK
    persistence   reading and writing files; no GTK
    services      library, daemon client, session integration; no GTK
    daemon        the background service; no GTK
    ui            GTK 4 and libadwaita
    application   the AdwApplication that hosts the interface
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
