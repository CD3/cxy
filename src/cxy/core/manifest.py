"""The cxy.toml model."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path, PurePosixPath
from typing import Any

from cxy.core.errors import CxyError

FILENAME = "cxy.toml"
TOOLS = ("clang-format", "clang-tidy", "cmake-lint", "cppcheck", "gersemi", "ruff")
TEST_FRAMEWORKS = ("catch2", "gtest")
STANDARDS = (11, 14, 17, 20, 23, 26)
RESERVED_NAMES = {"all", "clean", "help", "install", "package", "test"}
_PACKAGE_NAME = re.compile(r"[a-z][a-z0-9_-]*")
_TOOL_VERSION = re.compile(r"[0-9][0-9A-Za-z.]*")
_CONAN_NAME = re.compile(r"[a-z0-9_][a-z0-9_+.-]+")

OptionValue = str | bool | int


@dataclass(frozen=True)
class Dependency:
    name: str
    version: str
    options: Mapping[str, OptionValue] = field(default_factory=dict)
    force: bool = False
    cmake_package: str | None = None
    cmake_targets: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}/{self.version}"


@dataclass(frozen=True)
class Layout:
    lib: str = "lib"
    src: str = "src"
    include: str = "include"
    tests: str = "tests"
    build: str = "build"
    test_patterns: tuple[str, ...] = ("*_test.cpp", "*.test.cpp")


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    std: int = 20
    dependencies: tuple[Dependency, ...] = ()
    dev_dependencies: tuple[Dependency, ...] = ()
    overrides: Mapping[str, str] = field(default_factory=dict)
    layout: Layout = field(default_factory=Layout)
    test_framework: str | None = None
    """The framework of tests that do not show which they use."""

    tools: Mapping[str, str] = field(default_factory=dict)
    """Pinned versions of formatters and linters."""


def load(path: Path) -> Manifest:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise CxyError(f"cannot read {path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise CxyError(f"{path}: {error}") from error
    except CxyError as error:
        raise CxyError(f"{path}: {error}") from error


def parse(text: str) -> Manifest:
    data = tomllib.loads(text)
    _known(
        data,
        {"package", "dependencies", "dev-dependencies", "overrides", "layout", "test", "tools"},
        "top level",
    )
    package = _table(data, "package")
    _known(package, {"name", "version", "std"}, "[package]")
    name = _string(package.get("name"), "package.name")
    check_name(name)
    std = package.get("std", 20)
    if isinstance(std, bool) or std not in STANDARDS:
        raise CxyError(f"package.std must be one of {', '.join(map(str, STANDARDS))}")

    dependencies = _dependencies(data, "dependencies")
    dev_dependencies = _dependencies(data, "dev-dependencies")
    overrides = {
        _conan_name(key): _string(value, f"overrides.{key}")
        for key, value in _table(data, "overrides").items()
    }
    direct = {dependency.name for dependency in dependencies}
    if duplicate := sorted(direct & {dependency.name for dependency in dev_dependencies}):
        raise CxyError(f"{duplicate[0]!r} is in both dependencies and dev-dependencies")
    for dependency in (*dependencies, *dev_dependencies):
        if dependency.name in overrides:
            raise CxyError(
                f"{dependency.name!r} is a direct dependency, so it cannot be in [overrides]; "
                "set its version there and add force = true"
            )
    return Manifest(
        name=name,
        version=_string(package.get("version"), "package.version"),
        std=std,
        dependencies=dependencies,
        dev_dependencies=dev_dependencies,
        overrides=overrides,
        layout=_layout(_table(data, "layout")),
        test_framework=_test_framework(_table(data, "test")),
        tools=_tools(_table(data, "tools")),
    )


def check_name(name: str) -> None:
    if not _PACKAGE_NAME.fullmatch(name) or name in RESERVED_NAMES:
        raise CxyError(
            f"invalid package name {name!r}: use lowercase letters, digits, '-' and '_', "
            "starting with a letter, and avoid CMake's built-in target names"
        )


def check_dependency_name(name: str) -> None:
    _conan_name(name)


def _dependencies(data: Mapping[str, Any], section: str) -> tuple[Dependency, ...]:
    return tuple(
        _dependency(_conan_name(name), value, f"{section}.{name}")
        for name, value in _table(data, section).items()
    )


def _dependency(name: str, value: Any, where: str) -> Dependency:
    if isinstance(value, str):
        return Dependency(name, _string(value, where))
    if not isinstance(value, dict):
        raise CxyError(f"{where} must be a version string or a table")
    _known(value, {"version", "options", "force", "cmake"}, where)
    force = value.get("force", False)
    if not isinstance(force, bool):
        raise CxyError(f"{where}.force must be true or false")
    options = _table(value, "options", where)
    for key, option in options.items():
        if not isinstance(option, (str, bool, int)):
            raise CxyError(f"{where}.options.{key} must be a string, boolean or integer")
    cmake = _table(value, "cmake", where)
    _known(cmake, {"package", "targets"}, f"{where}.cmake")
    package = cmake.get("package")
    return Dependency(
        name=name,
        version=_string(value.get("version"), f"{where}.version"),
        options=options,
        force=force,
        cmake_package=None if package is None else _string(package, f"{where}.cmake.package"),
        cmake_targets=_strings(cmake.get("targets", []), f"{where}.cmake.targets"),
    )


def _layout(table: Mapping[str, Any]) -> Layout:
    directories = {f.name for f in fields(Layout)} - {"test_patterns"}
    _known(table, directories | {"test-patterns"}, "[layout]")
    values: dict[str, str] = {}
    for key in directories & table.keys():
        value = _string(table[key], f"layout.{key}")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
            raise CxyError(f"layout.{key} must be a relative path inside the project")
        values[key] = path.as_posix()
    if values.get("build") == ".":
        raise CxyError("layout.build cannot be the project directory")
    patterns = Layout.test_patterns
    if "test-patterns" in table:
        patterns = _strings(table["test-patterns"], "layout.test-patterns")
    layout = Layout(**values, test_patterns=patterns)
    build = PurePosixPath(layout.build)
    for key in ("lib", "src", "include", "tests"):
        if PurePosixPath(getattr(layout, key)).is_relative_to(build):
            raise CxyError(f"layout.{key} cannot be inside layout.build")
        # Only the project directory itself may contain the build directory.
        parent = getattr(layout, key)
        if parent != "." and build.is_relative_to(parent):
            raise CxyError(f"layout.build cannot be inside layout.{key}")
    return layout


def _test_framework(table: Mapping[str, Any]) -> str | None:
    _known(table, {"framework"}, "[test]")
    framework = table.get("framework")
    if framework is not None and framework not in TEST_FRAMEWORKS:
        raise CxyError(f"test.framework must be one of {', '.join(TEST_FRAMEWORKS)}")
    return framework


def _tools(table: Mapping[str, Any]) -> dict[str, str]:
    _known(table, set(TOOLS), "[tools]")
    for name, value in table.items():
        if not _TOOL_VERSION.fullmatch(_string(value, f"tools.{name}")):
            raise CxyError(f"tools.{name} must be an exact version, such as 18.1.8")
    return dict(table)


def _conan_name(name: str) -> str:
    if not _CONAN_NAME.fullmatch(name):
        raise CxyError(f"invalid Conan package name {name!r}")
    return name


def _known(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    if unknown := sorted(table.keys() - allowed):
        raise CxyError(f"unknown key {unknown[0]!r} in {where}")


def _table(data: Mapping[str, Any], key: str, where: str = "") -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise CxyError(f"{where + '.' if where else ''}{key} must be a table")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise CxyError(f"{where} must be a non-empty string")
    return value


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CxyError(f"{where} must be a list of strings")
    return tuple(_string(item, where) for item in value)
