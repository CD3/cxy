import subprocess
import sys
from pathlib import Path

import pytest

from cxy.core import formatting, tools
from cxy.core.errors import CxyError
from cxy.core.formatting import FORMATTERS
from cxy.core.manifest import parse
from cxy.core.workspace import Workspace

PACKAGE = '[package]\nname = "app"\nversion = "1"\n'
UGLY = {
    "lib/a.cpp": "int   a;\n",
    "CMakeLists.txt": "set( X   1 )\n",
    "conanfile.py": "a   =  'b'\n",
}


def write(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text.encode())


def read(root):
    return {name: (root / name).read_bytes().decode() for name in UGLY}


@pytest.fixture(params=["cxy", "git", "plain"])
def workspace(request, tmp_path):
    write(tmp_path, UGLY)
    if request.param == "cxy":
        write(tmp_path, {"cxy.toml": PACKAGE})
    elif request.param == "git":
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return Workspace.find(tmp_path)


def test_formats_every_kind_of_project(workspace):
    assert formatting.fmt(workspace, check=True) == 1
    assert formatting.fmt(workspace, diff=True) == 1
    assert read(workspace.root) == UGLY
    assert formatting.fmt(workspace) == 0
    assert read(workspace.root) == {
        "lib/a.cpp": "int a;\n",
        "CMakeLists.txt": "set(X 1)\n",
        "conanfile.py": 'a = "b"\n',
    }
    assert formatting.fmt(workspace, check=True) == 0


def test_diff_output(tmp_path, capsys):
    write(tmp_path, UGLY)
    formatting.fmt(Workspace(tmp_path), [str(tmp_path / "lib")], diff=True)
    out = capsys.readouterr().out
    assert "-int   a;\n+int a;\n" in out and "set" not in out


def test_syntax_error_fails_without_stopping_others(tmp_path):
    write(tmp_path, {**UGLY, "bad.cmake": "set(\n"})
    assert formatting.fmt(Workspace(tmp_path)) == 1
    assert (tmp_path / "bad.cmake").read_text() == "set(\n"
    assert (tmp_path / "lib/a.cpp").read_text() == "int a;\n"


def test_missing_tool_fails_without_stopping_others(tmp_path, monkeypatch):
    write(tmp_path, UGLY)
    monkeypatch.setenv("CXY_GERSEMI", "no-such-tool")
    assert formatting.fmt(Workspace(tmp_path)) == 1
    assert (tmp_path / "lib/a.cpp").read_text() == "int a;\n"


def test_ignored_file_is_left_alone(tmp_path):
    write(tmp_path, {**UGLY, ".clang-format-ignore": "lib/*\n"})
    assert formatting.fmt(Workspace(tmp_path)) == 0
    assert (tmp_path / "lib/a.cpp").read_text() == UGLY["lib/a.cpp"]


def test_configuration_is_found_from_the_file(tmp_path, monkeypatch):
    write(
        tmp_path, {"p/.gersemirc": "indent: 2\n", "p/sub/CMakeLists.txt": "if(A)\nb()\nendif()\n"}
    )
    monkeypatch.chdir(tmp_path)
    formatting.fmt(Workspace(tmp_path))
    assert "\n  b()\n" in (tmp_path / "p/sub/CMakeLists.txt").read_text()


def test_keeps_crlf(tmp_path):
    write(tmp_path, {name: text.replace("\n", "\r\n") for name, text in UGLY.items()})
    assert formatting.fmt(Workspace(tmp_path)) == 0
    assert all(text.endswith("\r\n") for text in read(tmp_path).values())
    assert formatting.fmt(Workspace(tmp_path), check=True) == 0


def test_paths(tmp_path, monkeypatch):
    write(tmp_path, {**UGLY, "notes.txt": "x"})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CxyError, match="do not know how"):
        formatting.fmt(Workspace(tmp_path), ["notes.txt"])
    with pytest.raises(CxyError, match="does not exist"):
        formatting.fmt(Workspace(tmp_path), ["missing.cpp"])


def test_git_workspace_skips_ignored_deleted_and_linked_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    write(tmp_path, {".gitignore": "out/\n", "out/x.cpp": "", "a.cpp": "", "gone.cpp": ""})
    if sys.platform != "win32":  # Links need special permission there.
        (tmp_path / "link.cpp").symlink_to("a.cpp")
    subprocess.run(["git", "add", "gone.cpp"], cwd=tmp_path, check=True)
    (tmp_path / "gone.cpp").unlink()
    (tmp_path / "sub").mkdir()
    workspace = Workspace.find(tmp_path / "sub")
    assert [p.name for p in workspace.files()] == [".gitignore", "a.cpp"]


def test_cxy_workspace_skips_the_build_directory(tmp_path):
    write(tmp_path, {"cxy.toml": PACKAGE, "build/cxy/CMakeLists.txt": "", "lib/a.cpp": ""})
    workspace = Workspace.find(tmp_path)
    expected = [tmp_path / "cxy.toml", tmp_path / "lib/a.cpp"]
    assert workspace.files() == expected
    assert workspace.files([str(tmp_path)]) == expected


def test_nearest_project_wins(tmp_path):
    write(tmp_path, {"cxy.toml": PACKAGE, "vendored/.git/HEAD": ""})
    assert Workspace.find(tmp_path / "vendored") == Workspace(tmp_path / "vendored")


def test_pins():
    manifest = parse(PACKAGE + '[tools]\nclang-format = "18.1.8"\n')
    assert manifest.tools == {"clang-format": "18.1.8"}
    for bad in ('gcc = "1"', 'ruff = ">=1"', "ruff = 1"):
        with pytest.raises(CxyError):
            parse(PACKAGE + f"[tools]\n{bad}\n")


def test_every_formatter_can_be_pinned():
    from cxy.core.manifest import TOOLS

    assert {f.tool for f in FORMATTERS} <= set(TOOLS)


def test_unformatted_kinds():
    assert not any(f.handles(Path(n)) for f in FORMATTERS for n in ("setup.py", "a.c", "a.txt"))


def test_relative_path_on_another_drive(monkeypatch):
    def fail(_):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr("os.path.relpath", fail)
    assert tools.relative(Path("a.cpp")) == "a.cpp"
