"""Kit as a desktop app: the same page as `kit ui`, in its own window.

The server from app.py runs on a background thread, and pywebview opens a
native window that shows its page. On macOS that window is the system's own
WebKit view and on Windows it is Edge's WebView2, so no browser is bundled.

pywebview is optional. Without it (or with --browser) the page opens in the
normal web browser, exactly like `kit ui`.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from .app import make_server
from .repo import KitError


class Bridge:
    """What the page is allowed to ask the window to do.

    pywebview makes this object's public methods callable from the page as
    `window.pywebview.api.<name>()`. The window itself is kept in a private
    attribute so that it is not exposed to the page as well.
    """

    def __init__(self, app=None):
        self._window = None
        self._app = app  # the App behind the page, to check which files may be dragged

    def pick_folder(self) -> str | None:
        """Show the system's folder chooser. Returns the chosen path, or None if cancelled."""
        import webview

        # pywebview 6 renamed the constant; fall back to the older spelling.
        dialogs = getattr(webview, "FileDialog", None)
        kind = dialogs.FOLDER if dialogs is not None else webview.FOLDER_DIALOG
        chosen = self._window.create_file_dialog(kind)
        return str(chosen[0]) if chosen else None

    def drag_sound(self, path: str) -> bool:
        """Start dragging a sound out of the window. Returns False where that is not supported."""
        file, _ = self._app.locate({"path": path})  # refuses .kit and paths outside the kit
        if sys.platform != "darwin":
            return False  # Windows and Linux: use the Show in Explorer button instead
        from . import dragout_mac

        dragout_mac.start(self._window.native, file)
        return True


def _load_webview():
    try:
        import webview
    except ImportError:
        return None
    return webview


def run(folder=None, window: bool = True, open_browser: bool = True, dev: bool = False) -> None:
    """Start the server and show the page, in a window if possible."""
    server, app = make_server(folder, dev=dev)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webview = _load_webview() if window else None
        if webview is None:
            if window:
                print("pywebview is not installed, so Kit is opening in your browser.")
                print("For its own window: pip install pywebview")
            print(f"Kit is running at {url}  (press Ctrl+C here to stop)", flush=True)
            if open_browser:
                webbrowser.open(url)
            threading.Event().wait()  # sleep until Ctrl+C
        else:
            bridge = Bridge(app)
            bridge._window = webview.create_window(
                "Kit", url, js_api=bridge, width=1180, height=820, min_size=(760, 560))
            # debug=True adds "Inspect Element" to the right-click menu: the browser's
            # developer tools, for working on the page.
            webview.start(debug=dev)  # returns when the window is closed
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.shutdown()
        server.server_close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kit-app", description="Kit in its own window.")
    parser.add_argument("folder", nargs="?", help="a kit folder to open (default: the home screen)")
    parser.add_argument("--browser", action="store_true", help="use the web browser, not a window")
    parser.add_argument("--dev", action="store_true", help="reload when ui.html is saved, with developer tools")
    parser.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)  # for tests
    # A double-clicked app can be handed extra arguments by the system; ignore them.
    args, _ = parser.parse_known_args(argv)
    try:
        run(args.folder, window=not (args.browser or args.no_open), open_browser=not args.no_open, dev=args.dev)
    except KitError as err:
        print(f"kit-app: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
