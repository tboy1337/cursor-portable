"""Interactive setup. The window is the only module that imports tkinter.

``prepare_tkinter`` runs on package import so Linux and macOS point Tcl at
this interpreter before ``window`` imports tkinter.
"""

from cursor_portable.wizard.tcl_path import prepare_tkinter

prepare_tkinter()
