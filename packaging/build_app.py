"""Build the double-click app with PyInstaller.

    pip install pyinstaller pywebview
    python packaging/build_app.py

The result lands in dist/: Kit.app on macOS, Kit/Kit.exe on Windows. An app is
built for the system this script runs on, so the Windows app has to be built
on Windows.

PyInstaller copies Python itself, the kit package and pywebview into the app,
so the person using it needs nothing installed. The page (ui.html) is not
Python code, so it has to be listed by hand with --add-data.
"""

import os
import sys
from pathlib import Path

import PyInstaller.__main__

try:
    import webview  # noqa: F401  (only checking that it is installed)
except ImportError:
    sys.exit("pywebview is not installed, so the app would have no window: pip install pywebview")

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "src" / "kit" / "ui.html"

PyInstaller.__main__.run([
    str(ROOT / "packaging" / "kit_app.py"),
    "--name", "Kit",
    "--windowed",                 # no Terminal window alongside the app
    "--noconfirm",                # overwrite the previous build
    "--paths", str(ROOT / "src"),
    "--add-data", f"{PAGE}{os.pathsep}kit",   # app.py looks for ui.html beside itself
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    *sys.argv[1:],                # extra PyInstaller options, if you pass any
])
