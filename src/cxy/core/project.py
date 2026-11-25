"""Locate a project and classify its source files."""

import os
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from cxy.core import manifest as manifests
from cxy.core.errors import CxyError
from cxy.core.manifest import Manifest

SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".c++"}
HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx"}


@dataclass(frozen=True)
class Sources:
    """Source paths relative to the project root, in POSIX form."""

    lib: tuple[str, ...] = ()
    app: tuple[str, ...] = ()
    main: str | None = None
    unit_tests: tuple[str, ...] = ()
    """Tests beside the code they test. Each becomes its own program."""
    tests: tuple[str, ...] = ()
    """Sources in the tests directory. Together they become one program."""
    has_headers: bool = False

    @property
    def has_library(self) -> bool:
        return bool(self.lib) or self.has_headers


@dataclass(frozen=True)
class Project:
    root: Path
    manifest: Manifest

    @classmethod
    def find(cls, start: Path | None = None) -> "Project":
        start = (start or Path.cwd()).resolve()
        for directory in (start, *start.parents):
            if (directory / manifests.FILENAME).is_file():
                return cls(directory, manifests.load(directory / manifests.FILENAME))
        raise CxyError(f"no {manifests.FILENAME} in {start} or any parent directory")

    @property
    def build_dir(self) -> Path:
        """Where cxy keeps its files, apart from other users of the build directory.

        An exported project builds in ``build/Debug``, which must not collide with ours on
        a case-insensitive filesystem.
        """
        return self.root / self.manifest.layout.build / "cxy"

    def sources(self) -> Sources:
        layout = self.manifest.layout
        roles = [
            (PurePosixPath(getattr(layout, role)), role)
            for role in ("tests", "src", "lib", "include")
        ]
        lib: list[str] = []
        app: list[str] = []
        unit_tests: list[str] = []
        tests: list[str] = []
        main: str | None = None
        has_headers = False
        for path in self._files():
            # The deepest configured directory containing the file decides its role.
            matches = [(d, role) for d, role in roles if path.is_relative_to(d)]
            if not matches:
                continue
            depth = max(len(d.parts) for d, _ in matches)
            owners = {role for d, role in matches if len(d.parts) == depth}
            if path.suffix in HEADER_SUFFIXES:
                has_headers = has_headers or "include" in owners
                continue
            name = path.as_posix()
            is_test = any(fnmatchcase(path.name, p) for p in layout.test_patterns)
            if "tests" in owners:
                tests.append(name)
            elif is_test:
                unit_tests.append(name)
            elif "src" in owners and path == PurePosixPath(layout.src) / f"main{path.suffix}":
                if main is not None:
                    raise CxyError(f"both {main} and {name} look like the application's main file")
                main = name
            elif "lib" in owners:
                lib.append(name)
            elif "src" in owners:
                app.append(name)
        return Sources(
            lib=tuple(lib),
            app=tuple(app),
            main=main,
            unit_tests=tuple(unit_tests),
            tests=tuple(tests),
            has_headers=has_headers,
        )

    def _files(self) -> list[PurePosixPath]:
        found: list[PurePosixPath] = []
        layout = self.manifest.layout
        visited: set[Path] = set()
        for top in sorted({layout.lib, layout.src, layout.include, layout.tests}):
            for current, directories, names in os.walk(self.root / top):
                here = Path(current)
                directories[:] = sorted(
                    d
                    for d in directories
                    if not d.startswith(".") and here / d != self.build_dir.parent
                )
                if here in visited:
                    continue
                visited.add(here)
                for name in names:
                    if Path(name).suffix in SOURCE_SUFFIXES | HEADER_SUFFIXES:
                        relative = (here / name).relative_to(self.root)
                        found.append(PurePosixPath(relative.as_posix()))
        return sorted(found)
