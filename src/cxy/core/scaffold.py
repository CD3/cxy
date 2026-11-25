"""Create new projects."""

import re
import shutil
import subprocess
from collections.abc import Set
from fnmatch import fnmatchcase
from pathlib import Path

from cxy.core import manifest as manifests
from cxy.core import testing, tools
from cxy.core.errors import CxyError
from cxy.core.project import SOURCE_SUFFIXES, Project

_MANIFEST = """\
[package]
name = "{name}"
version = "0.1.0"
std = 20

[dependencies]

[dev-dependencies]
"""

_FRAMEWORK_VERSIONS = {"catch2": "3.7.1", "gtest": "1.17.0"}

_CLANG_FORMAT = """\
BasedOnStyle: Google
IndentWidth: 4
ColumnLimit: 100
AllowShortFunctionsOnASingleLine: Empty
"""

_CLANG_TIDY = """\
Checks: >
  clang-diagnostic-*,
  clang-analyzer-*,
  bugprone-*,
  performance-*
"""

_HEADER = """\
#pragma once

#include <string>
#include <string_view>

namespace {ns} {{

std::string greet(std::string_view name);

}}  // namespace {ns}
"""

_SOURCE = """\
#include "{name}/greeting.hpp"

namespace {ns} {{

std::string greet(std::string_view name) {{
    return "Hello, " + std::string(name) + "!";
}}

}}  // namespace {ns}
"""

_MAIN = """\
#include <iostream>

#include "{name}/greeting.hpp"

int main(int argc, char** argv) {{
    std::cout << {ns}::greet(argc > 1 ? argv[1] : "world") << '\\n';
}}
"""

_TEST = """\
#include "{name}/greeting.hpp"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("greet includes the name") {{
    REQUIRE({ns}::greet("cxy") == "Hello, cxy!");
}}
"""


def new(path: Path, *, name: str | None = None, lib_only: bool = False) -> None:
    if path.exists():
        raise CxyError(f"{path} already exists; use 'cxy init' inside it instead")
    name = _resolve_name(path, name)
    path.mkdir(parents=True)
    _create(path, name, lib_only)


def init(path: Path, *, name: str | None = None, lib_only: bool = False) -> None:
    if (path / manifests.FILENAME).exists():
        raise CxyError(f"{path / manifests.FILENAME} already exists")
    _create(path, _resolve_name(path, name), lib_only)


def _resolve_name(path: Path, name: str | None) -> str:
    name = name or path.resolve().name
    manifests.check_name(name)
    return name


def _create(path: Path, name: str, lib_only: bool) -> None:
    files = {".gitignore": "/build/\n/CMakeUserPresets.json\n"}
    frameworks: Set[str]
    # Leave existing code alone: only add the sample if no source directory exists.
    if not any((path / d).exists() for d in ("lib", "src", "include", "tests")):
        files[".clang-format"] = _CLANG_FORMAT
        files[".clang-tidy"] = _CLANG_TIDY
        files[f"include/{name}/greeting.hpp"] = _HEADER
        files["lib/greeting.cpp"] = _SOURCE
        files["tests/greeting_test.cpp"] = _TEST
        if not lib_only:
            files["src/main.cpp"] = _MAIN
        frameworks = {"catch2"}
    else:
        frameworks = _used_frameworks(path, name)
    files[manifests.FILENAME] = _MANIFEST + "".join(
        f'{framework} = "{_FRAMEWORK_VERSIONS[framework]}"\n' for framework in sorted(frameworks)
    )
    namespace = re.sub(r"-", "_", name)
    for relative, template in files.items():
        target = path / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.format(name=name, ns=namespace), encoding="utf-8", newline="")
    _git_init(path)
    tools.status("Created", f"{'library' if lib_only else 'application'} {name!r}")


def _used_frameworks(path: Path, name: str) -> set[str]:
    """Return the test frameworks that existing tests include."""
    project = Project(path.resolve(), manifests.parse(_MANIFEST.format(name=name)))
    layout = project.manifest.layout
    found: set[str] = set()
    for file in project.files():
        relative = file.relative_to(project.root)
        is_test = relative.is_relative_to(layout.tests) or any(
            fnmatchcase(file.name, pattern) for pattern in layout.test_patterns
        )
        if is_test and file.suffix in SOURCE_SUFFIXES:
            found |= testing.detect(project, relative.as_posix())
    return found


def _git_init(path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        return
    inside = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"], cwd=path, capture_output=True
    )
    if inside.returncode:
        subprocess.run([git, "init", "--quiet"], cwd=path, check=False)
