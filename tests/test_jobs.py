"""
Background jobs for the windows: work runs on the pool's threads, results
come back on the main thread under their keys, and nothing comes after
`close()`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui.jobs import Deliver, Jobs


class Results:
    """`done` signals, and the threads they arrived and their work ran on."""

    def __init__(self, jobs: Jobs) -> None:
        self.got: list[tuple[object, object]] = []
        self.arrived_on: set[int] = set()
        jobs.done.connect(self.add)

    def add(self, key: object, result: object) -> None:
        self.got.append((key, result))
        self.arrived_on.add(threading.get_ident())


@pytest.fixture(name="jobs")
def fixture_jobs() -> Iterator[Jobs]:
    jobs = Jobs(2)
    yield jobs
    jobs.close()


def test_results_come_back_on_the_ui_thread(qtbot: QtBot, jobs: Jobs) -> None:
    results = Results(jobs)
    ran_on: list[int] = []

    def work() -> str:
        ran_on.append(threading.get_ident())
        return "listed"

    jobs.run("folder", work)
    qtbot.waitUntil(lambda: bool(results.got))
    assert results.got == [("folder", "listed")]
    assert results.arrived_on == {threading.get_ident()}
    assert threading.get_ident() not in ran_on


def test_a_loop_delivers_any_number_of_results(qtbot: QtBot, jobs: Jobs) -> None:
    results = Results(jobs)

    def work(deliver: Deliver) -> None:
        for n in range(3):
            deliver(n, n * n)

    jobs.run_loop(work)
    qtbot.waitUntil(lambda: len(results.got) == 3)
    assert sorted(results.got, key=str) == [(0, 0), (1, 1), (2, 4)]


def test_a_failing_job_is_logged_and_delivers_nothing(
    qtbot: QtBot, jobs: Jobs, caplog: pytest.LogCaptureFixture
) -> None:
    results = Results(jobs)

    def work() -> object:
        raise ValueError("broken")

    jobs.run("key", work)
    jobs.wait()
    qtbot.wait(20)
    assert not results.got
    assert "A background job failed" in caplog.text


def test_nothing_comes_after_close(qtbot: QtBot) -> None:
    jobs = Jobs(1)
    results = Results(jobs)
    gate = threading.Event()
    started = threading.Event()

    def held() -> str:
        started.set()
        assert gate.wait(10)
        return "late"

    jobs.run("held", held)
    jobs.run("queued", lambda: "never")
    assert started.wait(10)
    gate.set()
    jobs.close()  # the queued one is dropped; the held one's result too
    jobs.run("after", lambda: "never")
    qtbot.wait(50)
    assert not results.got
