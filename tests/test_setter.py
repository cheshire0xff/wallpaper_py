from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py.core.model import (
    ImageMode,
    Look,
    MonitorId,
    MonitorNotAttachedError,
    Rectangle,
)
from wallpaper_py.core.setter import apply_wallpaper, find_attached

from .fakes import FakeBackend, make_monitor


@pytest.fixture(name="image")
def fixture_image(tmp_path: Path) -> Path:
    path = tmp_path / "wall.png"
    Image.new("RGB", (800, 600), (10, 20, 30)).save(path)
    return path


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend(
        [
            make_monitor("Main", x=0, size=(3840, 2160)),
            make_monitor("FHD", x=-1920, size=(1920, 1080)),
            make_monitor("Strip", x=3840, size=(1920, 480)),
        ]
    )


def main_id(backend: FakeBackend) -> MonitorId:
    return backend.monitors()[0].id


class TestFindAttached:
    def test_matches_id_case_insensitively(self, backend: FakeBackend) -> None:
        monitor_id = main_id(backend)
        upper = MonitorId(str(monitor_id).upper())
        assert find_attached(backend, upper).name == "Main"

    def test_detached_monitor_is_rejected(self, backend: FakeBackend) -> None:
        monitor_id = main_id(backend)
        backend.detach("Main")
        with pytest.raises(MonitorNotAttachedError, match="Main"):
            find_attached(backend, monitor_id)

    def test_unknown_monitor_is_rejected(self, backend: FakeBackend) -> None:
        with pytest.raises(MonitorNotAttachedError):
            find_attached(backend, MonitorId("nope"))


class TestApplyWallpaper:
    @pytest.mark.parametrize("mode", list(ImageMode))
    def test_the_backend_gets_an_exact_size_bmp_render(
        self, backend: FakeBackend, image: Path, tmp_path: Path, mode: ImageMode
    ) -> None:
        strip_id = backend.monitors()[2].id
        used = apply_wallpaper(backend, strip_id, image, Look(mode), tmp_path)
        assert used.parent == tmp_path and used != image
        assert backend.set_calls[0].image == used
        with Image.open(used) as img:
            assert (img.format, img.size, img.mode) == ("BMP", (1920, 480), "RGB")

    def test_the_look_is_used(self, image: Path, tmp_path: Path) -> None:
        backend = FakeBackend([make_monitor("A", size=(400, 100))])
        fit = apply_wallpaper(
            backend, main_id(backend), image, Look(ImageMode.FIT), tmp_path
        )
        with Image.open(fit) as img:
            assert img.getpixel((5, 50)) == (0, 0, 0)  # a bar beside the image
            assert img.getpixel((200, 50)) != (0, 0, 0)

    def test_renders_alternate_between_two_files(
        self, backend: FakeBackend, image: Path, tmp_path: Path
    ) -> None:
        mid = main_id(backend)
        first = apply_wallpaper(backend, mid, image, Look(), tmp_path)
        second = apply_wallpaper(backend, mid, image, Look(), tmp_path)
        third = apply_wallpaper(backend, mid, image, Look(), tmp_path)
        assert first != second
        assert third == first

    def test_monitors_get_separate_render_files(
        self, backend: FakeBackend, image: Path, tmp_path: Path
    ) -> None:
        ids = [m.id for m in backend.monitors()]
        used = {apply_wallpaper(backend, i, image, Look(), tmp_path) for i in ids}
        assert len(used) == 3

    def test_detached_monitor_is_not_rendered_or_set(
        self, backend: FakeBackend, image: Path, tmp_path: Path
    ) -> None:
        mid = main_id(backend)
        backend.detach("Main")
        with pytest.raises(MonitorNotAttachedError):
            apply_wallpaper(backend, mid, image, Look(), tmp_path / "r")
        assert not backend.set_calls
        assert not (tmp_path / "r").exists()

    def test_monitor_detaching_while_rendering_is_not_set(
        self, backend: FakeBackend, image: Path, tmp_path: Path
    ) -> None:
        """A monitor switched off between the check and the set."""
        mid = main_id(backend)
        backend.before_monitors = lambda n: backend.detach("Main") if n == 3 else None
        with pytest.raises(MonitorNotAttachedError):
            apply_wallpaper(backend, mid, image, Look(), tmp_path)
        assert not backend.set_calls

    def test_resolution_change_while_rendering_is_not_set(
        self, backend: FakeBackend, image: Path, tmp_path: Path
    ) -> None:
        mid = main_id(backend)
        backend.before_monitors = lambda n: (
            backend.update("Main", rect=Rectangle(0, 0, 2560, 1440)) if n == 3 else None
        )
        with pytest.raises(MonitorNotAttachedError, match="resolution"):
            apply_wallpaper(backend, mid, image, Look(), tmp_path)
        assert not backend.set_calls
