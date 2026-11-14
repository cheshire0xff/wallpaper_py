"""
The Settings page's modal questions and pickers (settings.py), as one
`Dialogs` the page is given: tests replace them with answers of their own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFileDialog,
    QMessageBox,
    QWidget,
)

Choice = Literal["save", "discard", "cancel"]


def ask_unsaved(parent: QWidget) -> Choice:
    buttons = QMessageBox.StandardButton
    box = QMessageBox(
        QMessageBox.Icon.Question,
        "Unsaved changes",
        "Save the changes to the settings before closing?",
        buttons.Save | buttons.Discard | buttons.Cancel,
        parent,
    )
    box.setDefaultButton(buttons.Save)
    answer = box.exec()
    if answer == buttons.Save:
        return "save"
    return "discard" if answer == buttons.Discard else "cancel"


def choose_folder(parent: QWidget, start: str) -> str | None:
    chosen = QFileDialog.getExistingDirectory(parent, "Choose the folder", start)
    return chosen or None


def choose_list_file(parent: QWidget, start: str) -> str | None:
    chosen, _ = QFileDialog.getSaveFileName(
        parent,
        "Choose the list file",
        start,
        "Text files (*.txt);;All files (*)",
        options=QFileDialog.Option.DontConfirmOverwrite,
    )
    return chosen or None


def choose_colour(dialog: QColorDialog) -> bool:
    """
    Run the page's colour dialog (made and connected by the page, so it
    can follow the colour as it moves); True if one was chosen (OK).
    """
    return dialog.exec() == QDialog.DialogCode.Accepted


@dataclass(frozen=True)
class Dialogs:
    unsaved: Callable[[QWidget], Choice] = field(default=ask_unsaved)
    folder: Callable[[QWidget, str], str | None] = field(default=choose_folder)
    list_file: Callable[[QWidget, str], str | None] = field(default=choose_list_file)
    colour: Callable[[QColorDialog], bool] = field(default=choose_colour)
