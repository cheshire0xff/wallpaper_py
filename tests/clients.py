"""
Socket clients for tests, run in a child process like the real CLI.

They can't run in a thread of the test process: PySide keeps the GIL while
a blocking QLocalSocket waits, so the server under test (on the main
thread) would never get to accept the connection.
"""

import subprocess
import sys
from pathlib import Path

from pytestqt.qtbot import QtBot

# argv: server name, command, config, timeout. Prints the reply as JSON,
# NONE (no app running) or ERROR <message>.
SEND = """
import sys
from pathlib import Path
from wallpaper_py.app import ipc
from wallpaper_py.protocol import Envelope, Request
name, command, config, timeout = sys.argv[1:5]
envelope = Envelope(request=Request(command=command), config=Path(config))
try:
    reply = ipc.send(name, envelope, timeout=float(timeout))
except ipc.WallpaperError as err:
    print("ERROR", err)
else:
    print("NONE" if reply is None else ipc.encode(reply).decode().strip())
"""

# argv: server name, raw request line. Prints the raw reply line.
RAW = """
import sys
from PySide6.QtNetwork import QLocalSocket
socket = QLocalSocket()
socket.connectToServer(sys.argv[1])
assert socket.waitForConnected(5000)
socket.write(sys.argv[2].encode() + b"\\n")
socket.waitForBytesWritten(5000)
data = b""
while b"\\n" not in data and socket.waitForReadyRead(10000):
    data += socket.readAll().data()
print(data.decode().strip())
"""

# argv: data dir, then CLI arguments. The backend factory fails, which
# proves that a playlist command went to the running app; so does starting
# an app (which would be the real one).
CLI = """
import sys
from functools import partial
from pathlib import Path
from wallpaper_py import cli
from wallpaper_py.core import paths
data_dir = Path(sys.argv[1])
paths.data_dir = lambda: data_dir
def no_backend():
    raise SystemExit("backend used: the command did not go to the app")
def no_app(config):
    raise SystemExit("no app running: the CLI would start one")
sys.exit(
    cli.main(
        sys.argv[2:],
        backend_factory=no_backend,
        app_client=partial(cli.send_to_app, app_command=no_app),
    )
)
"""

# argv: the repository, data dir, config. The app with a fake backend, as
# CLI_STARTING starts it. Quits when a file "stop" appears in the data dir
# (or after a minute, should a test fail).
APP = """
import sys
from contextlib import nullcontext
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication, QTimer
from wallpaper_py.app.runtime import AppOptions, Platform, run_app
from wallpaper_py.core import paths
from tests.fakes import FakeBackend, FakePresence, make_monitor
data_dir, config = Path(sys.argv[2]), Path(sys.argv[3])
for name in ("data_dir", "config_dir", "cache_dir"):
    setattr(paths, name, lambda: data_dir)
paths.log_file = lambda: data_dir / "wallpaper.log"
app = QCoreApplication(sys.argv[:1])
stop = QTimer()
stop.timeout.connect(lambda: (data_dir / "stop").exists() and app.quit())
stop.start(100)
QTimer.singleShot(60_000, app.quit)
backend = FakeBackend([make_monitor("Main")])
platform = Platform(lambda: backend, FakePresence, thread_scope=nullcontext)
sys.exit(run_app(AppOptions(config, tray=False), platform))
"""

# argv: data dir, then CLI arguments. Like CLI, but if no app is running,
# the CLI starts APP (detached, like the real tray app).
CLI_STARTING = f"""
import sys
from functools import partial
from pathlib import Path
from wallpaper_py import cli
from wallpaper_py.core import paths
APP = {APP!r}
ROOT = {str(Path(__file__).parents[1])!r}
data_dir = Path(sys.argv[1])
paths.data_dir = lambda: data_dir
paths.log_file = lambda: data_dir / "wallpaper.log"
def app_command(config):
    return [sys.executable, "-c", APP, ROOT, str(data_dir), str(config)]
def no_backend():
    raise SystemExit("backend used: the command did not go to the app")
sys.exit(
    cli.main(
        sys.argv[2:],
        backend_factory=no_backend,
        app_client=partial(cli.send_to_app, app_command=app_command),
    )
)
"""


def run_python(
    qtbot: QtBot, code: str, *args: str, timeout_ms: int = 30_000
) -> subprocess.CompletedProcess[str]:
    """Run `code` in a child Python, keeping this thread's event loop going."""
    with subprocess.Popen(
        [sys.executable, "-c", code, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            qtbot.waitUntil(lambda: process.poll() is not None, timeout=timeout_ms)
        finally:
            if process.poll() is None:
                process.kill()
        out, err = process.communicate()
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)
