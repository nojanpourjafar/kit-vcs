"""Drag a sound out of Kit's window into FL Studio, Explorer or any other app (Windows).

The macOS version is in dragout_mac.py; this is the same idea with Windows'
own tools. On Windows, pywebview's window is a Windows Forms window, and
Windows Forms can start a drag carrying a list of files: the same kind of data
Explorer offers when a file is dragged out of it, so FL Studio takes it the
same way. pythonnet, which pywebview already installs on Windows, lets Python
call into .NET.

Only desktop.py imports this, and only on Windows.
"""

from __future__ import annotations

from pathlib import Path

import clr

clr.AddReference("System.Windows.Forms")

from System import Array, Func, String, Type  # noqa: E402
from System.Windows.Forms import DataFormats, DataObject, DragDropEffects  # noqa: E402


def file_drop(path: Path) -> DataObject:
    """The data Explorer offers when one file is dragged out of it."""
    return DataObject(DataFormats.FileDrop, Array[String]([str(path)]))


def start(form, path: Path) -> None:
    """Start dragging `path` out of `form` (pywebview's window).

    The page asks from a background thread, but a drag has to start on the
    window's own thread, so it is handed over with BeginInvoke. BeginInvoke
    does not wait: the drag runs until the mouse button is let go, and the
    page does not need to wait for that.
    """
    data = file_drop(path)

    def drag():
        form.DoDragDrop(data, DragDropEffects.Copy)  # copy: the kit keeps its file

    form.BeginInvoke(Func[Type](drag))
