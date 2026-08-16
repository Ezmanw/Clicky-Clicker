"""Editing the key list of a Key Combination action.

A combination is ordered, not a set: the executor presses in order and releases
in reverse, so ``Ctrl`` then ``C`` is a real chord while ``C`` then ``Ctrl`` is
not.  The editor therefore shows a list with reordering rather than a set of
checkboxes.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ...models import keys
from ..widgets.rows import icon_button, plain
from .key_chooser import choose_key

__all__ = ["ComboEditorDialog", "edit_combo"]


class ComboEditorDialog(Adw.Dialog):
    """Modal editor for an ordered list of keys."""

    def __init__(
        self,
        selected: list[str],
        on_changed: Callable[[list[str]], None],
    ) -> None:
        super().__init__()
        self.set_title("Edit Combination")
        self.set_content_width(440)
        self.set_content_height(460)

        self._codes = list(selected)
        self._on_changed = on_changed

        self._group = Adw.PreferencesGroup(
            title="Keys",
            description=(
                "Pressed in this order and released in reverse, so modifiers "
                "belong at the top."
            ),
        )

        add = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Key"))
        add.add_css_class("flat")
        add.connect("clicked", self._on_add)
        self._group.set_header_suffix(add)

        self._preview = Adw.PreferencesGroup(title="Preview")
        self._preview_row = plain(Adw.ActionRow(title="Nothing selected"))
        self._preview_row.add_prefix(Gtk.Image(icon_name="input-keyboard-symbolic"))
        self._preview.add(self._preview_row)

        page = Adw.PreferencesPage()
        page.add(self._preview)
        page.add(self._group)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Edit Combination"))
        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda *_: self.close())
        header.pack_end(done)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

        self._rows: list[Adw.ActionRow] = []
        self._rebuild()

    # --- Rendering ------------------------------------------------------

    def _rebuild(self) -> None:
        for row in self._rows:
            self._group.remove(row)
        self._rows = []

        for index, code in enumerate(self._codes):
            row = plain(Adw.ActionRow(), title=keys.label_for(code), subtitle=code)
            row.add_prefix(
                Gtk.Image(
                    icon_name=(
                        "input-mouse-symbolic"
                        if keys.is_mouse_button(code)
                        else "input-keyboard-symbolic"
                    )
                )
            )

            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            controls.set_valign(Gtk.Align.CENTER)
            controls.add_css_class("linked")

            up = icon_button("go-up-symbolic", "Move up")
            up.set_sensitive(index > 0)
            up.connect("clicked", self._on_move, index, -1)
            controls.append(up)

            down = icon_button("go-down-symbolic", "Move down")
            down.set_sensitive(index < len(self._codes) - 1)
            down.connect("clicked", self._on_move, index, 1)
            controls.append(down)

            row.add_suffix(controls)

            remove = icon_button("user-trash-symbolic", "Remove this key")
            remove.add_css_class("flat")
            remove.connect("clicked", self._on_remove, index)
            row.add_suffix(remove)

            self._group.add(row)
            self._rows.append(row)

        self._preview_row.set_title(
            keys.format_combo(self._codes) if self._codes else "Nothing selected"
        )
        self._preview_row.set_subtitle(
            f"{len(self._codes)} key(s)" if self._codes else "Add at least one key"
        )

    # --- Commands -------------------------------------------------------

    def _commit(self) -> None:
        self._rebuild()
        self._on_changed(list(self._codes))

    def _on_add(self, _button: Gtk.Button) -> None:
        def chosen(name: str) -> None:
            self._codes.append(name)
            self._commit()

        choose_key(self, title="Add Key to Combination", on_chosen=chosen)

    def _on_remove(self, _button: Gtk.Button, index: int) -> None:
        if 0 <= index < len(self._codes):
            del self._codes[index]
            self._commit()

    def _on_move(self, _button: Gtk.Button, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= index < len(self._codes) and 0 <= target < len(self._codes):
            self._codes[index], self._codes[target] = self._codes[target], self._codes[index]
            self._commit()


def edit_combo(
    parent: Gtk.Widget, selected: list[str], on_changed: Callable[[list[str]], None]
) -> ComboEditorDialog:
    """Open the combination editor anchored to *parent*."""
    dialog = ComboEditorDialog(selected, on_changed)
    dialog.present(parent)
    return dialog
