"""Create new projects."""

import re
import shutil
import subprocess
from pathlib import Path

from cxy.core import manifest as manifests
from cxy.core import tools
from cxy.core.errors import CxyError

_MANIFEST = """\
[package]
name = "{name}"
version = "0.1.0"
std = 20

[dependencies]

[dev-dependencies]
catch2 = "3.7.1"
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
    files = {manifests.FILENAME: _MANIFEST, ".gitignore": "/build/\n/CMakeUserPresets.json\n"}
    # Leave existing code alone: only add the sample if no source directory exists.
    if not any((path / d).exists() for d in ("lib", "src", "include", "tests")):
        files[f"include/{name}/greeting.hpp"] = _HEADER
        files["lib/greeting.cpp"] = _SOURCE
        files["tests/greeting_test.cpp"] = _TEST
        if not lib_only:
            files["src/main.cpp"] = _MAIN
    namespace = re.sub(r"-", "_", name)
    for relative, template in files.items():
        target = path / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.format(name=name, ns=namespace), encoding="utf-8")
    _git_init(path)
    tools.status("Created", f"{'library' if lib_only else 'application'} {name!r}")


def _git_init(path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        return
    inside = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"], cwd=path, capture_output=True
    )
    if inside.returncode:
        subprocess.run([git, "init", "--quiet"], cwd=path, check=False)
