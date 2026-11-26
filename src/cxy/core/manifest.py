"""The cxy.toml model."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
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
_DEFINE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(=.*)?", re.DOTALL)

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
class Settings:
    """How to compile something."""

    defines: tuple[str, ...] = ()
    """Preprocessor definitions, as NAME or NAME=value."""
    flags: tuple[str, ...] = ()
    """Compiler options, passed as they are."""


@dataclass(frozen=True)
class LibrarySettings(Settings):
    shared: bool = False


class Kind(StrEnum):
    LIBRARY = "library"
    PROGRAM = "program"


@dataclass(frozen=True)
class Component:
    """Where to find the files of a library or program that the project builds."""

    name: str
    kind: Kind
    directory: str
    """Its sources and private headers."""
    include: str | None = None
    """A library's public headers."""
    main: str | None = None
    """A program's main file, without its suffix, which may be that of any source."""
    uses: tuple[str, ...] = ()
    """The libraries that it links."""
    settings: Settings = Settings()
    """Its definitions reach whatever uses it; its flags apply only to its own sources."""
    shared: bool = False

    @property
    def target(self) -> str:
        return f"{self.name}_lib" if self.kind is Kind.LIBRARY else self.name

    @property
    def objects(self) -> str:
        """The target for a program's sources apart from its main file, which tests link."""
        return f"{self.name}_app"


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

    test_working_directory: str | None = None
    """Where tests run, relative to the project root; by default, the build directory."""

    test_separate: bool = False
    """Whether each test in the tests directory is its own program."""

    tools: Mapping[str, str] = field(default_factory=dict)
    """Pinned versions of formatters and linters."""

    build: Settings = Settings()
    """Settings for everything the project compiles, tests included."""
    library: LibrarySettings = LibrarySettings()
    program: Settings = Settings()

    @property
    def components(self) -> tuple[Component, ...]:
        """The libraries, then the programs. [layout] describes one of each."""
        layout = self.layout
        return (
            Component(
                self.name,
                Kind.LIBRARY,
                layout.lib,
                include=layout.include,
                settings=Settings(self.library.defines, self.library.flags),
                shared=self.library.shared,
            ),
            Component(
                self.name,
                Kind.PROGRAM,
                layout.src,
                main=PurePosixPath(layout.src, "main").as_posix(),
                uses=(self.name,),
                settings=self.program,
            ),
        )

    @property
    def include_directories(self) -> tuple[str, ...]:
        """Where the project's own includes are found, after the including file's directory."""
        components = self.components
        public = (c.include for c in components if c.include is not None)
        return (*public, *(c.directory for c in components))


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
        {
            "package",
            "dependencies",
            "dev-dependencies",
            "overrides",
            "layout",
            "test",
            "tools",
            "build",
            "lib",
            "bin",
        },
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
        test_working_directory=_test_working_directory(_table(data, "test")),
        test_separate=_boolean(_table(data, "test").get("separate", False), "test.separate"),
        tools=_tools(_table(data, "tools")),
        build=Settings(**_settings(_table(data, "build"), "build")),
        library=LibrarySettings(**_settings(_table(data, "lib"), "lib", frozenset({"shared"}))),
        program=Settings(**_settings(_table(data, "bin"), "bin")),
    )


def is_definition(text: str) -> bool:
    """Whether a preprocessor definition is NAME or NAME=value."""
    return _DEFINE.fullmatch(text) is not None


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
    force = _boolean(value.get("force", False), f"{where}.force")
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


def _settings(
    table: Mapping[str, Any], where: str, extra: frozenset[str] = frozenset()
) -> dict[str, Any]:
    _known(table, {"defines", "flags", *extra}, f"[{where}]")
    defines = _strings(table.get("defines", []), f"{where}.defines")
    for define in defines:
        if not is_definition(define):
            raise CxyError(f"{where}.defines: {define!r} is not NAME or NAME=value")
    flags = _strings(table.get("flags", []), f"{where}.flags")
    for flag in flags:
        if flag.startswith(("-D", "/D")):
            raise CxyError(f"{where}.flags: put {flag!r} in {where}.defines, without -D")
    values: dict[str, Any] = {"defines": defines, "flags": flags}
    for key in extra & table.keys():
        values[key] = _boolean(table[key], f"{where}.{key}")
    return values


def _layout(table: Mapping[str, Any]) -> Layout:
    directories = {f.name for f in fields(Layout)} - {"test_patterns"}
    _known(table, directories | {"test-patterns"}, "[layout]")
    values: dict[str, str] = {}
    for key in directories & table.keys():
        values[key] = _inside(table[key], f"layout.{key}")
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


def _inside(value: Any, where: str) -> str:
    """Return a path inside the project, relative to its root."""
    text = _string(value, where)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text or ":" in text:
        raise CxyError(f"{where} must be a relative path inside the project")
    return path.as_posix()


def _test_working_directory(table: Mapping[str, Any]) -> str | None:
    if "working-directory" not in table:
        return None
    return _inside(table["working-directory"], "test.working-directory")


def _test_framework(table: Mapping[str, Any]) -> str | None:
    _known(table, {"framework", "separate", "working-directory"}, "[test]")
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


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise CxyError(f"{where} must be true or false")
    return value


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CxyError(f"{where} must be a list of strings")
    return tuple(_string(item, where) for item in value)
