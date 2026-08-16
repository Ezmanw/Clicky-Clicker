"""Small helpers shared by the pages and dialogs."""

from __future__ import annotations

from typing import TypeVar

from gi.repository import GLib, Gtk

__all__ = ["plain", "escape", "icon_button"]

_T = TypeVar("_T")


def plain(widget: _T, *, title: str | None = None, subtitle: str | None = None) -> _T:
    """Turn off Pango markup on a widget, then set its text.

    The text must be applied *after* ``use-markup`` is cleared, which is why
    this takes it as arguments rather than leaving it to the constructor:
    ``Adw.ActionRow(title=...)`` sets the title while markup is still enabled,
    so the warning happens before the property can be turned off.

    ``AdwPreferencesRow``, ``AdwBanner`` and ``AdwToast`` interpret their text
    as markup by default.  Everything shown here is plain text that the user or
    the system supplied -- a macro named ``Fire & Ice``, a key labelled ``<``, a
    device name, an error message -- and none of it is ever meant as markup.

    Interpreting it would be both a rendering bug (Pango refuses to parse it and
    the label comes out empty) and a small injection risk, since an imported
    preset could otherwise smuggle markup into the interface.
    """
    widget.set_use_markup(False)  # type: ignore[attr-defined]
    if title is not None:
        widget.set_title(title)  # type: ignore[attr-defined]
    if subtitle is not None:
        widget.set_subtitle(subtitle)  # type: ignore[attr-defined]
    return widget


def escape(text: str) -> str:
    """Escape text for a widget that interprets markup and cannot be told not to.

    ``AdwNavigationPage:title`` is the case that matters: it is rendered as
    markup and, unlike ``AdwPreferencesRow``, exposes no ``use-markup``
    property, so a macro named ``Fire & Ice`` would otherwise render as an empty
    title with a Pango warning. Prefer :func:`plain` wherever it is available.
    """
    return GLib.markup_escape_text(text)


def icon_button(icon_name: str, tooltip: str) -> Gtk.Button:
    """A flat icon button carrying an accessible name as well as a tooltip."""
    button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    return button
