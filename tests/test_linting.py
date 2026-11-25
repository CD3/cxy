import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cxy.core import linting
from cxy.core.errors import CxyError
from cxy.core.linting import find_database, lint, parse
from cxy.core.workspace import Workspace

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
needs_tidy = pytest.mark.skipif(not shutil.which("clang-tidy"), reason="needs clang-tidy")
NULL = "int f() {\n  int* p = nullptr;\n  return *p;\n}\n"


@pytest.fixture(autouse=True)
def without_cmake_lint(monkeypatch):
    monkeypatch.setenv("CXY_CMAKE_LINT", "no-such-tool")


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def _make(files, compiled=()):
        for name, text in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text)
        if compiled:
            entries = [
                {
                    "directory": str(tmp_path),
                    "file": name,
                    "arguments": ["c++", "-std=c++20", f"-I{tmp_path}/include", "-c", name],
                }
                for name in compiled
            ]
            (tmp_path / "build").mkdir(exist_ok=True)
            (tmp_path / "build" / linting.DATABASE).write_text(json.dumps(entries))
        return Workspace(tmp_path)

    return _make


def test_parse_clang_tidy():
    output = (
        "/r/a.cpp:1:2: warning: bad thing [bugprone-x,-warnings-as-errors]\n"
        "    1 | code [x]\n"
        "/r/a.cpp:1:1: note: because\n"
        "/r/b/../c.hpp:3:4: error: worse [clang-diagnostic-error]\n"
    )
    first, second = parse(linting._TIDY, output, Path("/"))
    assert (first.line, first.column, first.check) == (1, 2, "bugprone-x,-warnings-as-errors")
    assert "because" in first.detail
    assert second.path == Path("/r/c.hpp").resolve() and second.severity == "error"


def test_parse_others():
    (ruff,) = parse(linting._RUFF, "q.py:1:8: F401 [*] `os` unused\nFound 1 error.\n", Path("/r"))
    assert (ruff.path, ruff.check) == (Path("/r/q.py").resolve(), "F401")
    output = (
        "CMakeLists.txt\n====\nCMakeLists.txt:39: [C0301] Line too long\nx.cmake:1,2: [W01] y\n"
    )
    long, other = parse(linting._CMAKE_LINT, output, Path("/r"))
    assert (long.line, long.column, long.check) == (39, 0, "C0301")
    assert (other.line, other.column) == (1, 2)


def test_conanfile(make, capsys):
    workspace = make({"conanfile.py": "import os\n"})
    assert lint(workspace) == 1
    assert "conanfile.py:1:8: warning: " in capsys.readouterr().out
    assert lint(workspace, fix=True) == 0
    assert lint(workspace) == 0


def test_conanfile_syntax_error(make):
    assert lint(make({"conanfile.py": "a = (\n"})) == 1


@needs_tidy
def test_reports_source_and_header_once(make, capsys):
    files = {
        "include/h.hpp": "inline int g() {\n}\n",
        "a.cpp": '#include "h.hpp"\n' + NULL,
        "b.cpp": '#include "h.hpp"\n',
    }
    workspace = make(files, ["a.cpp", "b.cpp"])
    assert lint(workspace) == 1
    out = capsys.readouterr().out
    assert out.count("a.cpp:4:10: warning") == 1
    assert out.count("include/h.hpp:2:1: warning") == 1
    assert lint(workspace, ["b.cpp"]) == 0
    assert lint(workspace, ["include/h.hpp"]) == 1


@needs_tidy
def test_compile_error_is_a_finding(make):
    assert lint(make({"a.cpp": "int f() { return x; }\n"}, ["a.cpp"])) == 1


@needs_tidy
def test_error_outside_the_selection_is_a_failure(make, capsys):
    files = {"include/h.hpp": "int f() { return x; }\n", "a.cpp": '#include "h.hpp"\n'}
    assert lint(make(files, ["a.cpp"]), ["a.cpp"]) == 1
    assert "not everything could be checked" in capsys.readouterr().err


@needs_tidy
def test_fix_stays_within_the_selection(make, tmp_path):
    files = {
        ".clang-tidy": "Checks: '-*,modernize-use-nullptr'\n",
        "include/h.hpp": "inline int* g() { return 0; }\n",
        "a.cpp": '#include "h.hpp"\nint* f() { return 0; }\n',
    }
    workspace = make(files, ["a.cpp"])
    assert lint(workspace, ["a.cpp"], fix=True) == 0
    assert "return nullptr" in (tmp_path / "a.cpp").read_text()
    assert (tmp_path / "include/h.hpp").read_text() == files["include/h.hpp"]
    assert lint(workspace, fix=True) == 0
    assert "return nullptr" in (tmp_path / "include/h.hpp").read_text()


@needs_tidy
def test_respects_header_filter_in_config(make):
    files = {
        ".clang-tidy": "Checks: '-*,modernize-use-nullptr'\nHeaderFilterRegex: 'nothing'\n",
        "include/h.hpp": "inline int* g() { return 0; }\n",
        "a.cpp": '#include "h.hpp"\n',
    }
    assert lint(make(files, ["a.cpp"])) == 0


@posix_only
def test_no_database_fails_but_checks_the_rest(make, capsys, monkeypatch):
    monkeypatch.setenv("CXY_CLANG_TIDY", "true")
    assert lint(make({"a.cpp": "", "conanfile.py": "import os\n"})) == 1
    captured = capsys.readouterr()
    assert "F401" in captured.out and "no compile_commands.json" in captured.err


def test_missing_tool_fails(make, capsys, monkeypatch):
    monkeypatch.setenv("CXY_CLANG_TIDY", "no-such-tool")
    assert lint(make({"a.cpp": ""}, ["a.cpp"])) == 1
    assert "uv tool install clang-tidy" in capsys.readouterr().err


@posix_only
def test_crashing_tool_fails(make, capsys, monkeypatch, tmp_path):
    fake = tmp_path / "fake"
    fake.write_text('#!/bin/sh\n[ "$1" = --version ] && exit 0\necho boom >&2; exit 3\n')
    fake.chmod(0o755)
    monkeypatch.setenv("CXY_CLANG_TIDY", str(fake))
    assert lint(make({"a.cpp": ""}, ["a.cpp"])) == 1
    assert "boom" in capsys.readouterr().err


@posix_only
def test_source_missing_from_database(make, capsys, monkeypatch):
    monkeypatch.setenv("CXY_CLANG_TIDY", "true")
    workspace = make({"a.cpp": "", "b.cpp": ""}, ["a.cpp"])
    assert lint(workspace) == 0
    assert "skipped 1 file" in capsys.readouterr().err
    assert lint(workspace, ["b.cpp"]) == 1


def test_find_database_prefers_the_newest(tmp_path):
    assert find_database(tmp_path) is None
    import os

    for number, name in enumerate(["build/Debug", "build/Release"]):
        path = tmp_path / name / linting.DATABASE
        path.parent.mkdir(parents=True)
        path.touch()
        os.utime(path, (100 - number, 100 - number))
    assert find_database(tmp_path) == tmp_path / "build/Debug" / linting.DATABASE


def test_unknown_file(make):
    workspace = make({"a.txt": ""})
    with pytest.raises(CxyError, match="do not know how"):
        lint(workspace, ["a.txt"])


def test_git_project_without_cpp_needs_no_database(make, tmp_path):
    make({"conanfile.py": ""})
    subprocess.run(["git", "init", "-q"], check=True)
    assert lint(Workspace.find(tmp_path)) == 0
