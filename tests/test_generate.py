import ast
import json

import pytest

from cxy.core import cmake, conan
from cxy.core.conan import CMakeInfo
from cxy.core.errors import CxyError
from cxy.core.manifest import parse
from cxy.core.project import Sources
from cxy.core.stamp import Stamp, write_if_changed

MANIFEST = parse("""
[package]
name = "app"
version = "1.0"
[dependencies]
fmt = { version = "11.0.2", force = true, options = { header_only = true } }
[dev-dependencies]
catch2 = "3.7.1"
[overrides]
zlib = "1.3.1"
""")


def test_conanfile_traits():
    text = conan.conanfile(MANIFEST)
    ast.parse(text)
    assert "self.requires('fmt/11.0.2', force=True)" in text
    assert "self.requires('zlib/1.3.1', override=True)" in text
    assert "self.test_requires('catch2/3.7.1')" in text
    assert "'fmt/*:header_only': True" in text


def test_graph_names():
    node = {
        "name": "libx",
        "context": "host",
        "cpp_info": {
            "root": {"properties": {"cmake_file_name": "LibX"}},
        },
    }
    graph = conan.read_graph(json.dumps({"graph": {"nodes": {"1": node}}}))
    assert graph["libx"] == CMakeInfo("LibX", "libx::libx")


def test_cmake_app_objects_are_shared_with_tests():
    sources = Sources(
        lib=("lib/a.cpp",), app=("src/b.cpp",), main="src/main.cpp", tests=("tests/t.cpp",)
    )
    text = cmake.cmakelists(MANIFEST, sources, {"fmt": CMakeInfo("fmt", "fmt::fmt")}, root="../..")
    assert "add_library(app_app OBJECT" in text
    assert "target_link_libraries(app_lib PUBLIC fmt::fmt)" in text
    assert "target_link_libraries(app PRIVATE app_app app_lib)" in text
    assert "target_link_libraries(app_tests PRIVATE app_app app_lib Catch2::Catch2WithMain)" in text


def test_unit_tests_get_their_own_programs():
    sources = Sources(
        lib=("lib/a.cpp",),
        unit_tests=("lib/a_b_test.cpp", "lib/a/b_test.cpp"),
        tests=("tests/x.cpp", "tests/y.cpp"),
    )
    programs = cmake.test_programs(MANIFEST, sources)
    assert [p.target for p in programs] == [
        "app_lib_a_b_test",
        "app_lib_a_b_test_2",
        "app_tests",
    ]
    assert programs[-1].sources == ("tests/x.cpp", "tests/y.cpp")


def test_isolated_program_skips_the_library():
    sources = Sources(lib=("lib/a.cpp", "lib/b.cpp"), unit_tests=("lib/a_test.cpp",))
    isolated = {"lib/a_test.cpp": ["lib/a.cpp"]}
    text = cmake.cmakelists(MANIFEST, sources, {}, isolated=isolated)
    program = text[text.index("add_executable(app_lib_a_test_isolated") :]
    assert "lib/a.cpp" in program
    assert "lib/b.cpp" not in program and "app_lib " not in program


def test_standalone_files_do_not_depend_on_cxy():
    conanfile = conan.conanfile(MANIFEST, standalone=True)
    ast.parse(conanfile)
    assert 'check_min_cppstd(self, "20")' in conanfile
    cmakelists = cmake.cmakelists(MANIFEST, Sources(lib=("lib/a.cpp",)), {})
    assert "cxy" not in conanfile + cmakelists
    assert "ROOT_DIR" not in cmakelists


def test_cmake_errors():
    with pytest.raises(CxyError, match="nothing to build"):
        cmake.cmakelists(MANIFEST, Sources(), {})
    plain = parse('[package]\nname = "app"\nversion = "1"\n')
    with pytest.raises(CxyError, match="catch2"):
        cmake.cmakelists(plain, Sources(lib=("lib/a.cpp",), tests=("t.cpp",)), {})


def test_quote():
    assert cmake.quote('a"${b}') == '"a\\"\\${b}"'


def test_stamp_and_write_if_changed(tmp_path):
    path = tmp_path / "out"
    assert write_if_changed(path, "x")
    assert not write_if_changed(path, "x")
    stamp = Stamp(tmp_path / "stamp", "input")
    assert not stamp.is_current()
    stamp.save()
    assert stamp.is_current(path)
    assert not stamp.is_current(tmp_path / "missing")
    assert not Stamp(tmp_path / "stamp", "changed").is_current()
