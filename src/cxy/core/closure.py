"""Work out which sources a unit test needs by following its includes.

This assumes that the source implementing a header shares its name: ``net/http.hpp`` and
``net/http.cpp``. Code organized differently can still use the ordinary test programs.
"""

import os
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from cxy.core.project import Project, Sources

_INCLUDE = re.compile(r"""^\s*#\s*include\s*[<"]([^>"]+)[>"]""", re.MULTILINE)


def tested_name(project: Project, test: str) -> str | None:
    """Return "http" for "lib/net/http_test.cpp"."""
    filename = PurePosixPath(test).name
    for pattern in project.manifest.layout.test_patterns:
        if pattern.count("*") != 1:
            continue
        prefix, suffix = pattern.split("*")
        if filename.startswith(prefix) and filename.endswith(suffix):
            name = filename[len(prefix) : len(filename) - len(suffix)]
            if name:
                return name
    return None


def test_names(project: Project, test: str) -> set[str]:
    path = PurePosixPath(test)
    names = {test, path.name, path.stem}
    if name := tested_name(project, test):
        names.add(name)
    return names


def needed(project: Project, sources: Sources, test: str) -> tuple[str, ...]:
    """Return the project sources needed to link a unit test, excluding the test itself."""
    candidates = (*sources.lib, *sources.app)
    found: set[str] = set()
    pending = [test]
    if name := tested_name(project, test):
        pending += _implementations(str(PurePosixPath(test).with_name(name)), candidates)
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in candidates:
            found.add(current)
        for header in _includes(project, current):
            pending.append(header)
            pending += _implementations(header.rpartition(".")[0], candidates)
    return tuple(sorted(found))


def _implementations(stem: str, candidates: Sequence[str]) -> list[str]:
    """Return the sources named like a file, preferring the most similar directory."""
    wanted = PurePosixPath(stem)
    scored = []
    for candidate in candidates:
        path = PurePosixPath(candidate).with_suffix("")
        if path.name != wanted.name:
            continue
        # Compare directories from the file outwards: include/app/net and lib/net share "net".
        parts = zip(reversed(wanted.parent.parts), reversed(path.parent.parts), strict=False)
        score = 0
        for a, b in parts:
            if a != b:
                break
            score += 1
        scored.append((score, candidate))
    best = max((score for score, _ in scored), default=0)
    return [candidate for score, candidate in scored if score == best]


def _includes(project: Project, file: str) -> list[str]:
    layout = project.manifest.layout
    directories = [
        str(PurePosixPath(file).parent),
        layout.include,
        layout.lib,
        layout.src,
    ]
    text = (project.root / file).read_text(encoding="utf-8", errors="replace")
    headers = []
    for include in _INCLUDE.findall(text):
        for directory in directories:
            path = Path(os.path.normpath(project.root / directory / include))
            if path.is_relative_to(project.root) and path.is_file():
                headers.append(path.relative_to(project.root).as_posix())
                break
    return headers
