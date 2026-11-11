"""
The PySide6 app shell: tray icon, single instance, command socket, and the
playlist service on its own thread (see runtime.py); the windows run in a
process of their own (see ui/process.py).
"""

# Load the protocol models, and with them all of pydantic, before any Qt
# module. Once PySide6 is imported, its import hook probes every new module
# (`hasattr(module, "__wrapped__")`), which runs pydantic's lazy module
# `__getattr__` mid-import and fails with a circular import if pydantic was
# only partly loaded before Qt (seen with PySide6 6.12, pydantic 2.14).
from .. import protocol as _protocol  # pylint: disable=unused-import
