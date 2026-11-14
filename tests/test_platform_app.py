"""Screen debounce, the instance lock, and XDG start-at-login entries."""

from pathlib import Path

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.screens import ScreenWatcher
from wallpaper_py.backends.xdg import XdgAutostart, exec_line


def test_screen_changes_are_debounced(qtbot: QtBot, qapp: QApplication) -> None:
    watcher = ScreenWatcher(qapp, delay_ms=100)
    settled: list[bool] = []
    watcher.settled.connect(lambda: settled.append(True))
    for _ in range(3):
        watcher.poke()
        qtbot.wait(30)
    assert not settled
    qtbot.waitUntil(lambda: bool(settled))
    qtbot.wait(200)
    assert settled == [True]


def test_instance_lock(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "data" / "app.lock")
    second = InstanceLock(tmp_path / "data" / "app.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_xdg_autostart(tmp_path: Path) -> None:
    autostart = XdgAutostart(tmp_path / "autostart")
    assert not autostart.enabled()
    autostart.disable()  # nothing to remove: fine
    autostart.enable(["/usr/bin/python3", "-m", "wallpaper_py", "tray"])
    assert autostart.enabled()
    text = autostart.file.read_text(encoding="utf-8")
    assert "Exec=/usr/bin/python3 -m wallpaper_py tray\n" in text
    assert text.startswith("[Desktop Entry]\nType=Application\n")
    autostart.disable()
    assert not autostart.enabled()


def test_exec_line_quoting() -> None:
    line = exec_line(["/opt/my app/python", "--config", '/a "b"/$x%.toml'])
    # Quoted, `"` and `$` escaped, then backslashes doubled; % doubled.
    assert line == '"/opt/my app/python" --config "/a \\\\"b\\\\"/\\\\$x%%.toml"'
