"""Locate a project and classify its source files."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from cxy.core import manifest as manifests
from cxy.core.errors import CxyError
from cxy.core.manifest import Component, Kind, Manifest

SOURCE_SUFFIXES = {".cpp", ".cc", ".cxx", ".c++"}
HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx"}


@dataclass(frozen=True)
class ComponentSources:
    """The files found for a library or program, relative to the project root."""

    component: Component
    sources: tuple[str, ...] = ()
    """What it compiles, apart from a program's main file."""
    main: str | None = None
    has_headers: bool = False
    """Whether a library has public headers."""

    @property
    def exists(self) -> bool:
        if self.component.kind is Kind.LIBRARY:
            return bool(self.sources) or self.has_headers
        return self.main is not None


@dataclass(frozen=True)
class Sources:
    """Source paths relative to the project root, in POSIX form."""

    components: tuple[ComponentSources, ...] = ()
    """Every component of the layout, including those without files."""
    unit_tests: tuple[str, ...] = ()
    """Tests beside the code they test. Each becomes its own program."""
    tests: tuple[str, ...] = ()
    """Sources in the tests directory. Together they become one program."""

    @classmethod
    def default(
        cls,
        manifest: Manifest,
        *,
        lib: tuple[str, ...] = (),
        app: tuple[str, ...] = (),
        main: str | None = None,
        has_headers: bool = False,
        unit_tests: tuple[str, ...] = (),
        tests: tuple[str, ...] = (),
    ) -> "Sources":
        """Return the sources of a project with one library and one program."""
        library, program = manifest.components
        return cls(
            (
                ComponentSources(library, lib, has_headers=has_headers),
                ComponentSources(program, app, main),
            ),
            unit_tests,
            tests,
        )

    @property
    def libraries(self) -> list[ComponentSources]:
        return [c for c in self.components if c.component.kind is Kind.LIBRARY and c.exists]

    @property
    def programs(self) -> list[ComponentSources]:
        return [c for c in self.components if c.component.kind is Kind.PROGRAM and c.exists]

    @property
    def compiled(self) -> tuple[str, ...]:
        """The sources of every component, apart from main files."""
        return tuple(source for c in self.components for source in c.sources)


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
        manifest = self.manifest
        components = manifest.components
        # (directory, component index or None for the tests directory, whether it holds headers)
        claims: list[tuple[PurePosixPath, int | None, bool]] = [
            (PurePosixPath(manifest.layout.tests), None, False)
        ]
        for index, component in enumerate(components):
            claims.append((PurePosixPath(component.directory), index, False))
            if component.include is not None:
                claims.append((PurePosixPath(component.include), index, True))
        found: list[list[str]] = [[] for _ in components]
        mains: list[str | None] = [None for _ in components]
        headers = [False for _ in components]
        unit_tests: list[str] = []
        tests: list[str] = []
        for path in self._files():
            # The deepest configured directory containing the file decides its role.
            matches = [claim for claim in claims if path.is_relative_to(claim[0])]
            if not matches:
                continue
            depth = max(len(d.parts) for d, _, _ in matches)
            owners = [(index, public) for d, index, public in matches if len(d.parts) == depth]
            if path.suffix in HEADER_SUFFIXES:
                for index, public in owners:
                    if public and index is not None:
                        headers[index] = True
                continue
            name = path.as_posix()
            owned = sorted({index for index, public in owners if index is not None and not public})
            if any(index is None for index, _ in owners):
                tests.append(name)
            elif any(fnmatchcase(path.name, p) for p in manifest.layout.test_patterns):
                unit_tests.append(name)
            elif (
                program := next(
                    (i for i in owned if components[i].main == path.with_suffix("").as_posix()),
                    None,
                )
            ) is not None:
                if (main := mains[program]) is not None:
                    raise CxyError(f"both {main} and {name} look like the application's main file")
                mains[program] = name
            elif owned:
                found[owned[0]].append(name)
        return Sources(
            tuple(
                ComponentSources(component, tuple(found[i]), mains[i], headers[i])
                for i, component in enumerate(components)
            ),
            tuple(unit_tests),
            tuple(tests),
        )

    def files(self) -> list[Path]:
        """Return every file in the layout directories and directly in the root."""
        found = {path for path in self.root.iterdir() if path.is_file()}
        for top in self._tops():
            found.update(self._walk(top))
        return sorted(found)

    def _tops(self) -> set[str]:
        manifest = self.manifest
        return {manifest.layout.tests, *manifest.include_directories}

    def _files(self) -> list[PurePosixPath]:
        found = {
            PurePosixPath(path.relative_to(self.root).as_posix())
            for top in self._tops()
            for path in self._walk(top)
            if path.suffix in SOURCE_SUFFIXES | HEADER_SUFFIXES
        }
        return sorted(found)

    def _walk(self, top: str) -> Iterator[Path]:
        for current, directories, names in os.walk(self.root / top):
            here = Path(current)
            directories[:] = [
                d
                for d in directories
                if not d.startswith(".") and here / d != self.build_dir.parent
            ]
            for name in names:
                yield here / name
