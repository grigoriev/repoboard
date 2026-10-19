"""macOS process identity: the name and icon in Cmd-Tab and the Dock, outside an app bundle."""

from __future__ import annotations

import sys
from importlib import resources


def icon_path() -> str:
    return str(resources.files("repoboard") / "resources" / "icon.png")


def brand_process(menu_bar_only: bool) -> None:  # pragma: no cover - needs AppKit
    """Name the process Repoboard and give it the app icon. A menu bar process stays out of Cmd-Tab."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSImage
        from Foundation import NSBundle
    except ImportError:
        return
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = "Repoboard"
    app = NSApplication.sharedApplication()
    app.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon_path()))
    if menu_bar_only:
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
