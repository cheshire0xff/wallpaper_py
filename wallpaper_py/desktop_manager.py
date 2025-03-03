import platform

if platform.system() == "Windows":
    from .windows.desktop_manager import DesktopManager

__all__ = ["DesktopManager"]
