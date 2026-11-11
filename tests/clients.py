"""
Socket clients for tests, run in a child process like the real CLI.

They can't run in a thread of the test process: PySide keeps the GIL while
a blocking QLocalSocket waits, so the server under test (on the main
thread) would never get to accept the connection.
"""

import subprocess
import sys

from pytestqt.qtbot import QtBot

# argv: server name, command, config, timeout. Prints the reply as JSON,
# NONE (no app running) or ERROR <message>.
SEND = """
import sys
from pathlib import Path
from wallpaper_py.app import ipc
from wallpaper_py.commands import Request
name, command, config, timeout = sys.argv[1:5]
envelope = ipc.Envelope(request=Request(command), config=Path(config))
try:
    reply = ipc.send(name, envelope, timeout=float(timeout))
except ipc.WallpaperError as err:
    print("ERROR", err)
else:
    print("NONE" if reply is None else ipc.encode_reply(reply).decode().strip())
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
# proves that a playlist command went to the running app.
CLI = """
import sys
from pathlib import Path
from wallpaper_py import cli
from wallpaper_py.core import paths
data_dir = Path(sys.argv[1])
paths.data_dir = lambda: data_dir
def no_backend():
    raise SystemExit("backend used: the command did not go to the app")
sys.exit(cli.main(sys.argv[2:], backend_factory=no_backend))
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
