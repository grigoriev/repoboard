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
    from AppKit import NSApplicationActivationPolicyRegular

    app = NSApplication.sharedApplication()
    if not getattr(sys, "frozen", False):
        # Outside the app bundle the process is Python: give it the name and icon of Repoboard.
        info = NSBundle.mainBundle().infoDictionary()
        with contextlib.suppress(TypeError, AttributeError):
            info["CFBundleName"] = "Repoboard"  # the dictionary is mutable in practice, but not by contract
        app.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon_path()))
    # The bundle is a menu bar agent (LSUIElement). Its window process must show in Cmd-Tab and the Dock.
    app.setActivationPolicy_(
        NSApplicationActivationPolicyAccessory if menu_bar_only else NSApplicationActivationPolicyRegular
    )


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


def is_fullscreen(window: object) -> bool:  # pragma: no cover - needs AppKit
    """Whether a pywebview window is in macOS fullscreen."""
    try:
        from AppKit import NSWindowStyleMaskFullScreen
    except ImportError:
        return False
    native = getattr(window, "native", None)
    return bool(native is not None and native.styleMask() & NSWindowStyleMaskFullScreen)


def in_app_bundle() -> bool:
    """Whether Repoboard runs as Repoboard.app. Only an app bundle can open at login."""
    return sys.platform == "darwin" and bool(getattr(sys, "frozen", False))


def opens_at_login() -> bool:  # pragma: no cover - needs ServiceManagement
    from ServiceManagement import SMAppService, SMAppServiceStatusEnabled

    return bool(SMAppService.mainAppService().status() == SMAppServiceStatusEnabled)


def set_open_at_login(enabled: bool) -> None:  # pragma: no cover - needs ServiceManagement
    from ServiceManagement import SMAppService

    service = SMAppService.mainAppService()
    if enabled:
        service.registerAndReturnError_(None)
    else:
        service.unregisterAndReturnError_(None)
