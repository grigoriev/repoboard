"""macOS process identity: the name and icon in Cmd-Tab and the Dock, outside an app bundle."""

from __future__ import annotations

import contextlib
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
    with contextlib.suppress(TypeError, AttributeError):
        info["CFBundleName"] = "Repoboard"  # the dictionary is mutable in practice, but not by contract
    app = NSApplication.sharedApplication()
    app.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon_path()))
    if menu_bar_only:
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def title_bar_height() -> float:  # pragma: no cover - needs AppKit
    """Height of a standard window title bar: the frame of a titled window minus its content."""
    if sys.platform != "darwin":
        return 0
    try:
        from AppKit import (
            NSMakeRect,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSWindowStyleMaskResizable,
            NSWindowStyleMaskTitled,
        )
    except ImportError:
        return 0
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )
    content = NSMakeRect(0, 0, 100, 100)
    return float(NSWindow.frameRectForContentRect_styleMask_(content, style).size.height - 100)
