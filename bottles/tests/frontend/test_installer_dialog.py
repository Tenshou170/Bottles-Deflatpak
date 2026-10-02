import os
# ruff: noqa: E402

from types import SimpleNamespace
from unittest.mock import Mock

from gi.repository import Gio

# The installer dialog module declares Gtk.Templates; registering the
# compiled resource first lets the real module import cleanly (and keeps
# this file independent of sibling test import order).
bottles_resource = Gio.Resource.load(
    os.environ.get("BOTTLES_TEST_RESOURCE", "build/bottles.gresource")
)
Gio.resources_register(bottles_resource)

from bottles.frontend.windows.installer import InstallerDialog


def test_installer_progress_displays_percentage():
    dialog = SimpleNamespace(progressbar=Mock())

    InstallerDialog.update_progress.__wrapped__(dialog, 0.42)

    dialog.progressbar.set_fraction.assert_called_once_with(0.42)
    dialog.progressbar.set_text.assert_called_once_with("42%")
    dialog.progressbar.set_show_text.assert_called_once_with(True)


def test_installer_progress_restores_overall_progress():
    dialog = SimpleNamespace(
        progressbar=Mock(),
        _InstallerDialog__current_step=2,
        _InstallerDialog__steps=4,
    )

    InstallerDialog.update_progress.__wrapped__(dialog, None)

    dialog.progressbar.set_fraction.assert_called_once_with(0.5)
    dialog.progressbar.set_show_text.assert_called_once_with(False)


def test_installer_activity_displays_manifest_label():
    dialog = SimpleNamespace(label_activity=Mock())

    InstallerDialog.update_activity.__wrapped__(dialog, "Microsoft 365 setup")

    dialog.label_activity.set_label.assert_called_once_with(
        "Running Microsoft 365 setup..."
    )
