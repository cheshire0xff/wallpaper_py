"""
The app code follows the Qt for Python rules that bit us or bite others
(see docs/architecture.md, Rules):

- no lambda holding `self` connected to a signal (or given to
  QTimer.singleShot): Qt keeps it, and with it the object, alive until the
  sender is deleted, which can free the object from inside that deletion;
- a method connected as `self.method` is a `@Slot` (or a signal): Qt for
  Python recommends it for every slot, and only real slots get the
  receiver's thread on a queued connection;
- no method shadows an inherited Qt method (PYSIDE-2990: such a slot was
  connected as a plain callable and ran in the sender's thread).
"""

import ast
import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from pathlib import Path

from PySide6.QtCore import QObject

import wallpaper_py.app

APP = Path(wallpaper_py.app.__file__).parent


def modules() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(APP.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), str(path))


def where(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(APP.parent)}:{getattr(node, 'lineno', '?')}"


def is_connect(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr in ("connect", "singleShot")


def test_no_lambdas_holding_self_on_signals() -> None:
    offenders = []
    for path, tree in modules():
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not is_connect(call):
                continue
            for argument in call.args:
                names = {n.id for n in ast.walk(argument) if isinstance(n, ast.Name)}
                if isinstance(argument, ast.Lambda | ast.Call) and "self" in names:
                    offenders.append(where(path, call))
    assert not offenders, f"lambda / partial holding self connected at {offenders}"


def is_slot(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Slot"
        for d in function.decorator_list
    )


class Members:
    """Per class (by name, across the app): its slots, plain methods, signals."""

    def __init__(self) -> None:
        self.bases: dict[str, list[str]] = {}
        self.slots: dict[str, set[str]] = {}
        self.plain: dict[str, set[str]] = {}
        self.signals: dict[str, set[str]] = {}
        for _, tree in modules():
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                self._add(cls)

    def _add(self, cls: ast.ClassDef) -> None:
        name = cls.name
        self.bases[name] = [
            b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
            for b in cls.bases
        ]
        functions = [n for n in cls.body if isinstance(n, ast.FunctionDef)]
        self.slots[name] = {f.name for f in functions if is_slot(f)}
        self.plain[name] = {f.name for f in functions if not is_slot(f)}
        self.signals[name] = {
            target.id
            for node in cls.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "Signal"
            for target in node.targets
            if isinstance(target, ast.Name)
        }

    def kind(self, cls: str, member: str) -> str | None:
        """ "slot", "signal", "plain", or None if it's Qt's (not ours)."""
        for name in [cls, *self.bases.get(cls, [])]:
            if name not in self.bases:
                continue
            if member in self.slots[name]:
                return "slot"
            if member in self.signals[name]:
                return "signal"
            if member in self.plain[name]:
                return "plain"
            if name != cls and (found := self.kind(name, member)) is not None:
                return found
        return None


def test_methods_connected_as_slots_are_slots() -> None:
    members = Members()
    offenders = []
    for path, tree in modules():
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for call in ast.walk(cls):
                if not isinstance(call, ast.Call) or not is_connect(call):
                    continue
                for argument in call.args:
                    if (
                        isinstance(argument, ast.Attribute)
                        and isinstance(argument.value, ast.Name)
                        and argument.value.id == "self"
                        and members.kind(cls.name, argument.attr) == "plain"
                    ):
                        offenders.append(
                            f"{cls.name}.{argument.attr} ({where(path, call)})"
                        )
    assert not offenders, f"connected but not a @Slot: {offenders}"


def our_qobject_classes() -> Iterator[type[QObject]]:
    for info in pkgutil.walk_packages([str(APP)], "wallpaper_py.app."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and issubclass(cls, QObject):
                yield cls


def test_no_method_shadows_a_qt_method() -> None:
    offenders = []
    for cls in our_qobject_classes():
        qt_bases = [b for b in cls.__mro__ if b.__module__.startswith("PySide6")]
        for name, value in vars(cls).items():
            if name.startswith("__") or not callable(value):
                continue
            if name.endswith("Event"):  # event handlers are meant to override
                continue
            if any(name in vars(base) for base in qt_bases):
                offenders.append(f"{cls.__name__}.{name}")
    assert not offenders, f"shadows an inherited Qt method: {offenders}"


def test_the_checks_see_the_code() -> None:
    members = Members()
    assert members.kind("ServiceWorker", "tick") == "slot"
    assert members.kind("CommandServer", "_ready_read") == "slot"  # inherited
    assert members.kind("ServiceWorker", "changed") == "signal"
    assert members.kind("ServiceWorker", "deleteLater") is None  # Qt's
    assert len(list(our_qobject_classes())) >= 12
