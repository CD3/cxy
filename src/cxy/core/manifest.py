"""The cxy.toml model."""

import re
import tomllib
from collections.abc import Mapping, Sequence
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
SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".c++"}
HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx"}


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
    directory: str | None
    """Its sources and private headers. A program may have only a main file."""
    include: str | None = None
    """A library's public headers."""
    main: str | None = None
    """A program's main file. Without a suffix, it may have that of any source, and must be in
    the program's directory."""
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

    @property
    def where(self) -> str:
        """Where it is described in cxy.toml."""
        return f"{'lib' if self.kind is Kind.LIBRARY else 'bin'}.{self.name}"

    def is_main(self, path: PurePosixPath, owned: bool) -> bool:
        """Whether a file is this program's main file. ``owned`` is whether it is in the
        program's directory, rather than a deeper one of another component."""
        if self.main is None:
            return False
        main = PurePosixPath(self.main)
        if main.suffix in SOURCE_SUFFIXES:
            return path == main
        return owned and path.suffix in SOURCE_SUFFIXES and path.with_suffix("") == main


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
    """Settings for the library that [layout] describes."""
    program: Settings = Settings()
    """Settings for the program that [layout] describes."""
    libraries: tuple[Component, ...] = ()
    """Those in [lib.NAME] tables, which replace the one that [layout] describes."""
    programs: tuple[Component, ...] = ()
    """Those in [bin.NAME] tables, which replace the one that [layout] describes."""

    @property
    def components(self) -> tuple[Component, ...]:
        """The libraries, then the programs."""
        layout = self.layout
        libraries = self.libraries or (
            Component(
                self.name,
                Kind.LIBRARY,
                layout.lib,
                include=layout.include,
                settings=Settings(self.library.defines, self.library.flags),
                shared=self.library.shared,
            ),
        )
        programs = self.programs or (
            Component(
                self.name,
                Kind.PROGRAM,
                layout.src,
                main=PurePosixPath(layout.src, "main").as_posix(),
                uses=tuple(library.name for library in libraries),
                settings=self.program,
            ),
        )
        return (*libraries, *programs)

    @property
    def include_directories(self) -> tuple[str, ...]:
        """Where the project's own includes are found, after the including file's directory."""
        components = self.components
        public = (c.include for c in components if c.include is not None)
        return (*public, *(c.directory for c in components if c.directory is not None))


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
    layout = _layout(_table(data, "layout"))
    library, libraries = _libraries(_table(data, "lib"), layout)
    program, programs = _programs(_table(data, "bin"), layout, libraries or (name,))
    manifest = Manifest(
        name=name,
        version=_string(package.get("version"), "package.version"),
        std=std,
        dependencies=dependencies,
        dev_dependencies=dev_dependencies,
        overrides=overrides,
        layout=layout,
        test_framework=_test_framework(_table(data, "test")),
        test_working_directory=_test_working_directory(_table(data, "test")),
        test_separate=_boolean(_table(data, "test").get("separate", False), "test.separate"),
        tools=_tools(_table(data, "tools")),
        build=_settings(_table(data, "build"), "build", known=True),
        library=library,
        program=program,
        libraries=libraries,
        programs=programs,
    )
    _check_components(manifest.components)
    return manifest


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


def _settings(table: Mapping[str, Any], where: str, known: bool = False) -> Settings:
    if known:
        _known(table, {"defines", "flags"}, f"[{where}]")
    defines = _strings(table.get("defines", []), f"{where}.defines")
    for define in defines:
        if not is_definition(define):
            raise CxyError(f"{where}.defines: {define!r} is not NAME or NAME=value")
    flags = _strings(table.get("flags", []), f"{where}.flags")
    for flag in flags:
        if flag.startswith(("-D", "/D")):
            raise CxyError(f"{where}.flags: put {flag!r} in {where}.defines, without -D")
    return Settings(defines, flags)


def _named(table: Mapping[str, Any], where: str) -> dict[str, dict[str, Any]] | None:
    """Return the named components in a table, or None if it holds settings."""
    named = {key: value for key, value in table.items() if isinstance(value, dict)}
    if not named:
        return None
    if len(named) != len(table):
        example = next(iter(named))
        raise CxyError(
            f"[{where}] holds either settings or named components such as "
            f"[{where}.{example}], not both"
        )
    for name in named:
        if not _PACKAGE_NAME.fullmatch(name) or name in RESERVED_NAMES:
            raise CxyError(
                f"invalid name {where}.{name}: use lowercase letters, digits, '-' and '_', "
                "starting with a letter"
            )
    return named


def _libraries(
    table: Mapping[str, Any], layout: Layout
) -> tuple[LibrarySettings, tuple[Component, ...]]:
    named = _named(table, "lib")
    if named is None:
        _known(table, {"defines", "flags", "shared"}, "[lib]")
        settings = _settings(table, "lib")
        shared = _boolean(table.get("shared", False), "lib.shared")
        return LibrarySettings(settings.defines, settings.flags, shared), ()
    libraries = []
    for name, value in named.items():
        where = f"lib.{name}"
        _known(value, {"sources", "include", "uses", "shared", "defines", "flags"}, f"[{where}]")
        if "sources" not in value:
            raise CxyError(f"{where}.sources is required: the directory with the library's files")
        directory = _directory(value["sources"], f"{where}.sources", layout)
        include = directory
        if "include" in value:
            include = _directory(value["include"], f"{where}.include", layout)
        libraries.append(
            Component(
                name,
                Kind.LIBRARY,
                directory,
                include=include,
                uses=_strings(value.get("uses", []), f"{where}.uses"),
                settings=_settings(value, where),
                shared=_boolean(value.get("shared", False), f"{where}.shared"),
            )
        )
    return LibrarySettings(), tuple(libraries)


def _programs(
    table: Mapping[str, Any], layout: Layout, libraries: Sequence[Component | str]
) -> tuple[Settings, tuple[Component, ...]]:
    named = _named(table, "bin")
    if named is None:
        return _settings(table, "bin", known=True), ()
    names = tuple(lib if isinstance(lib, str) else lib.name for lib in libraries)
    programs = []
    for name, value in named.items():
        where = f"bin.{name}"
        _known(value, {"sources", "main", "uses", "defines", "flags"}, f"[{where}]")
        directory = None
        if "sources" in value:
            directory = _directory(value["sources"], f"{where}.sources", layout)
        if "main" in value:
            main = _inside(value["main"], f"{where}.main")
            if PurePosixPath(main).suffix not in SOURCE_SUFFIXES:
                raise CxyError(f"{where}.main must be a C++ source file")
        elif directory is not None:
            main = PurePosixPath(directory, "main").as_posix()
        else:
            raise CxyError(
                f"{where} needs main, the file with main(), or sources, a directory with main.cpp"
            )
        uses = _strings(value["uses"], f"{where}.uses") if "uses" in value else names
        programs.append(
            Component(
                name,
                Kind.PROGRAM,
                directory,
                main=main,
                uses=uses,
                settings=_settings(value, where),
            )
        )
    return Settings(), tuple(programs)


def _directory(value: Any, where: str, layout: Layout) -> str:
    directory = _inside(value, where)
    build = PurePosixPath(layout.build)
    if PurePosixPath(directory).is_relative_to(build):
        raise CxyError(f"{where} cannot be inside layout.build")
    if directory != "." and build.is_relative_to(directory):
        raise CxyError(f"layout.build cannot be inside {where}")
    return directory


def _check_components(components: Sequence[Component]) -> None:
    libraries = {c.name: c for c in components if c.kind is Kind.LIBRARY}
    for component in components:
        for used in component.uses:
            if used not in libraries:
                raise CxyError(f"{component.where}.uses: there is no library called {used!r}")
            if used == component.name and component.kind is Kind.LIBRARY:
                raise CxyError(f"{component.where}.uses: a library cannot use itself")

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            cycle = " -> ".join((*path[path.index(name) :], name))
            raise CxyError(f"libraries cannot use each other in a cycle: {cycle}")
        for used in libraries[name].uses:
            visit(used, (*path, name))

    for name in libraries:
        visit(name, ())

    seen: dict[str, str] = {}
    for component in components:
        targets = [component.target]
        if component.kind is Kind.PROGRAM:
            targets.append(component.objects)
        for target in targets:
            if target in seen:
                raise CxyError(
                    f"{seen[target]} and {component.where} would both make the target {target}"
                )
            seen[target] = component.where
    for kind in Kind:
        directories: dict[str, str] = {}
        for component in components:
            if component.kind is kind and component.directory is not None:
                if component.directory in directories:
                    raise CxyError(
                        f"{directories[component.directory]} and {component.where} have the same "
                        "sources directory"
                    )
                directories[component.directory] = component.where


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
