import pytest

from wallpaper_py.core.selection import MonitorSelectionError, resolve_monitor

from .fakes import GUID, make_monitor

MONITORS = [
    make_monitor("AW3225QF", pnp="DELA246"),
    make_monitor("PLX2783H", pnp="IVM6611"),
    make_monitor("ZeroMOD", pnp="___3132"),
]


@pytest.mark.parametrize(
    ("selector", "name"),
    [
        ("AW3225QF", "AW3225QF"),
        ("aw3225qf", "AW3225QF"),
        ("  zeromod ", "ZeroMOD"),
        ("zero", "ZeroMOD"),
        ("PLX", "PLX2783H"),
        (str(MONITORS[1].id), "PLX2783H"),
        (str(MONITORS[1].id).lower(), "PLX2783H"),
    ],
)
def test_resolves(selector: str, name: str) -> None:
    assert resolve_monitor(MONITORS, selector).name == name


def test_ambiguous_part_of_name() -> None:
    monitors = [make_monitor("Dell U2720Q"), make_monitor("Dell S2421")]
    with pytest.raises(MonitorSelectionError, match="several"):
        resolve_monitor(monitors, "dell")


def test_number_that_is_a_whole_name_matches() -> None:
    monitors = [make_monitor("2"), make_monitor("FHD")]
    assert resolve_monitor(monitors, "2").name == "2"


def test_identical_names_need_the_full_id() -> None:
    twins = [make_monitor("U2720Q", pnp="DEL1"), make_monitor("U2720Q", pnp="DEL2")]
    with pytest.raises(MonitorSelectionError, match="full id"):
        resolve_monitor(twins, "U2720Q")
    assert resolve_monitor(twins, str(twins[1].id)) is twins[1]


@pytest.mark.parametrize("selector", ["0", "1", " 2 ", "2783"])
def test_numbers_are_refused(selector: str) -> None:
    """Even "2783", a unique part of PLX2783H: numbers look like indices."""
    with pytest.raises(MonitorSelectionError, match="numbers are not supported"):
        resolve_monitor(MONITORS, selector)


@pytest.mark.parametrize("selector", [GUID, "DISPLAY", "DELA246"])
def test_parts_of_ids_do_not_match(selector: str) -> None:
    with pytest.raises(MonitorSelectionError, match="No monitor matches"):
        resolve_monitor(MONITORS, selector)


def test_empty_selector() -> None:
    with pytest.raises(MonitorSelectionError, match="Empty"):
        resolve_monitor(MONITORS, "   ")


def test_detached_monitors_can_be_selected() -> None:
    """Selection is about identity; refusing detached ones is the setter's job."""
    monitors = [make_monitor("Main", attached=False), make_monitor("FHD")]
    assert resolve_monitor(monitors, "main").name == "Main"
