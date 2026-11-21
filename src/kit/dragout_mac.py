"""Drag a sound out of Kit's window into FL Studio, Finder or any other app (macOS).

A web page cannot hand a real file to another program, so the page asks
Python, and this module starts the same kind of drag that Finder starts,
using Apple's own API through PyObjC (which pywebview already installs).

Only desktop.py imports this, and only on macOS.
"""

from __future__ import annotations

from pathlib import Path

import AppKit
import Foundation
import objc
from PyObjCTools import AppHelper

ICON_SIZE = 32


class KitDragSource(AppKit.NSObject):
    """The object macOS asks about the drag while it is going on."""

    # NSDragOperation draggingSession:sourceOperationMaskForDraggingContext:(NSDraggingContext)
    # The type string spells that out for PyObjC: returns an unsigned integer (Q),
    # takes self (@), the selector (:), the session (@) and an integer (q).
    @objc.typedSelector(b"Q@:@q")
    def draggingSession_sourceOperationMaskForDraggingContext_(self, session, context):
        return AppKit.NSDragOperationCopy  # the other app gets a copy; the kit keeps its file


_source = None  # kept alive here, because macOS does not hold on to the drag source


def start(window, path: Path) -> None:
    """Start dragging `path` from `window` (an NSWindow), under the mouse pointer.

    pywebview runs page requests on a background thread, but anything that
    touches the screen must run on the main thread, so the work is queued there.
    """
    AppHelper.callAfter(_begin, window, str(path))


def _begin(window, path: str) -> None:
    global _source
    event = AppKit.NSApp.currentEvent()
    pressed = (AppKit.NSEventTypeLeftMouseDown, AppKit.NSEventTypeLeftMouseDragged)
    if event is None or event.type() not in pressed:
        return  # the button was already let go, so there is nothing to drag
    if _source is None:
        _source = KitDragSource.alloc().init()

    view = window.contentView()
    item = AppKit.NSDraggingItem.alloc().initWithPasteboardWriter_(Foundation.NSURL.fileURLWithPath_(path))
    icon = AppKit.NSWorkspace.sharedWorkspace().iconForFile_(path)  # the same icon Finder shows
    point = view.convertPoint_fromView_(event.locationInWindow(), None)
    frame = Foundation.NSMakeRect(point.x - ICON_SIZE / 2, point.y - ICON_SIZE / 2, ICON_SIZE, ICON_SIZE)
    item.setDraggingFrame_contents_(frame, icon)
    view.beginDraggingSessionWithItems_event_source_([item], event, _source)
