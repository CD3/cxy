import ast
import json

import pytest

from cxy.core import cmake, conan
from cxy.core.conan import CMakeInfo
from cxy.core.errors import CxyError
from cxy.core.manifest import parse
from cxy.core.project import ComponentSources, Sources
from cxy.core.stamp import Stamp, write_if_changed
from cxy.core.testing import plan_programs

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


def programs_for(sources):
    return plan_programs(MANIFEST, sources, lambda _: set())


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
    sources = Sources.default(
        MANIFEST,
        lib=("lib/a.cpp",),
        app=("src/b.cpp",),
        main="src/main.cpp",
        tests=("tests/t.cpp",),
    )
    text = cmake.cmakelists(
        MANIFEST,
        sources,
        {"fmt": CMakeInfo("fmt", "fmt::fmt")},
        programs=programs_for(sources),
        root="../..",
    )
    assert "add_library(app_app OBJECT" in text
    assert "target_link_libraries(app_lib PUBLIC fmt::fmt)" in text
    assert "target_link_libraries(app PRIVATE app_app app_lib)" in text
    assert "target_link_libraries(app_tests PRIVATE Catch2::Catch2WithMain app_app app_lib)" in text


def test_unit_tests_get_their_own_programs():
    sources = Sources.default(
        MANIFEST,
        lib=("lib/a.cpp",),
        unit_tests=("lib/a_b_test.cpp", "lib/a/b_test.cpp"),
        tests=("tests/x.cpp", "tests/y.cpp"),
    )
    programs = programs_for(sources)
    assert [p.target for p in programs] == [
        "app_lib_a_b_test",
        "app_lib_a_b_test_2",
        "app_tests",
    ]
    assert programs[-1].sources == ("tests/x.cpp", "tests/y.cpp")


def test_isolated_program_skips_the_library():
    sources = Sources.default(
        MANIFEST, lib=("lib/a.cpp", "lib/b.cpp"), unit_tests=("lib/a_test.cpp",)
    )
    isolated = {"lib/a_test.cpp": ["lib/a.cpp"]}
    text = cmake.cmakelists(
        MANIFEST, sources, {}, programs=programs_for(sources), isolated=isolated
    )
    program = text[text.index("add_executable(app_lib_a_test_isolated") :]
    assert "lib/a.cpp" in program
    assert "lib/b.cpp" not in program and "app_lib " not in program


def test_settings_reach_what_needs_them():
    manifest = parse(
        """
[package]
name = "app"
version = "1.0"
[dev-dependencies]
catch2 = "3.7.1"
[build]
flags = ["-Wall"]
[lib]
shared = true
defines = ['A="x"']
flags = ["-O2"]
[bin]
defines = ["B"]
flags = ["-O1"]
"""
    )
    sources = Sources.default(
        manifest,
        lib=("lib/a.cpp",),
        app=("src/b.cpp",),
        main="src/main.cpp",
        unit_tests=("lib/a_test.cpp",),
    )
    programs = plan_programs(manifest, sources, lambda _: set())
    isolated = {"lib/a_test.cpp": ["lib/a.cpp"]}
    text = cmake.cmakelists(manifest, sources, {}, programs=programs, root="..", isolated=isolated)
    assert 'add_compile_options(\n  "-Wall"\n)' in text
    assert "add_library(app_lib SHARED" in text
    assert "WINDOWS_EXPORT_ALL_SYMBOLS ON" in text
    assert 'target_compile_definitions(app_lib PUBLIC\n  "A=\\"x\\""\n)' in text
    assert 'target_compile_options(app_lib PRIVATE\n  "-O2"\n)' in text
    # The tests link the objects, so the program's definitions are public there.
    assert 'target_compile_definitions(app_app PUBLIC\n  "B"\n)' in text
    assert 'target_compile_options(app PRIVATE\n  "-O1"\n)' in text
    program = text[text.index("add_executable(app_lib_a_test_isolated") :]
    assert '"A=\\"x\\""\n    "B"\n' in program and '"-O2"\n    "-O1"\n' in program


@pytest.mark.parametrize("shared", [True, False])
def test_others_choose_whether_the_library_is_shared(shared):
    manifest = parse(
        f'[package]\nname = "app"\nversion = "1.0"\n[lib]\nshared = {str(shared).lower()}\n'
    )
    sources = Sources.default(manifest, lib=("lib/a.cpp",))
    managed = cmake.cmakelists(manifest, sources, {}, root="..")
    assert f"add_library(app_lib {'SHARED' if shared else 'STATIC'}" in managed
    assert "BUILD_SHARED_LIBS" not in managed

    exported = cmake.cmakelists(manifest, sources, {})
    default = "ON" if shared else "OFF"
    assert f'option(BUILD_SHARED_LIBS "Build shared libraries" {default})' in exported
    assert "add_library(app_lib\n" in exported
    recipe = conan.conanfile(manifest, standalone=True, shared=cmake.shared_default(sources))
    assert 'options = {"shared": [True, False]}' in recipe
    assert f"'shared': {shared}," in recipe
    assert "shared" not in conan.conanfile(manifest, standalone=True)
    assert "shared" not in conan.conanfile(manifest, shared=True)


@pytest.mark.parametrize(
    ("extra", "message"),
    [("[lib]\nshared = true\n", "lib.shared"), ('[lib]\nflags = ["-O2"]\n', "build.flags")],
)
def test_settings_need_library_sources(extra, message):
    manifest = parse(f'[package]\nname = "app"\nversion = "1.0"\n{extra}')
    with pytest.raises(CxyError, match=message):
        cmake.cmakelists(manifest, Sources.default(manifest, has_headers=True), {})


def test_standalone_files_do_not_depend_on_cxy():
    conanfile = conan.conanfile(MANIFEST, standalone=True)
    ast.parse(conanfile)
    assert 'check_min_cppstd(self, "20")' in conanfile
    cmakelists = cmake.cmakelists(MANIFEST, Sources.default(MANIFEST, lib=("lib/a.cpp",)), {})
    assert "cxy" not in conanfile + cmakelists
    assert "ROOT_DIR" not in cmakelists and "WIN32" not in cmakelists


def test_cmake_errors():
    with pytest.raises(CxyError, match="nothing to build"):
        cmake.cmakelists(
            MANIFEST,
            Sources.default(
                MANIFEST,
            ),
            {},
        )
    plain = parse('[package]\nname = "app"\nversion = "1"\n')
    with pytest.raises(CxyError, match="catch2"):
        plan_programs(
            plain, Sources.default(MANIFEST, lib=("lib/a.cpp",), tests=("t.cpp",)), lambda _: set()
        )


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


def test_writes_line_endings_unchanged(tmp_path):
    path = tmp_path / "out"
    for text in ("a\nb\n", "a\r\nb\r\n"):
        assert write_if_changed(path, text)
        assert path.read_bytes() == text.encode()
        assert not write_if_changed(path, text)


def test_libraries_of_different_kinds():
    manifest = parse(
        """
[package]
name = "app"
version = "1.0"
[lib.core]
sources = "core"
[lib.text]
sources = "text"
uses = ["core"]
shared = true
[bin.tool]
main = "tool.cpp"
uses = ["core"]
"""
    )
    core, text, tool = manifest.components
    sources = Sources(
        (
            ComponentSources(core, ("core/a.cpp",)),
            ComponentSources(text, ("text/b.cpp",)),
            ComponentSources(tool, main="tool.cpp"),
        )
    )
    # Both forms name the kinds, since BUILD_SHARED_LIBS would make them alike.
    assert cmake.shared_default(sources) is None
    for root in (None, ".."):
        generated = cmake.cmakelists(manifest, sources, {}, root=root)
        assert "BUILD_SHARED_LIBS" not in generated
        assert "add_library(core_lib STATIC" in generated
        assert "OUTPUT_NAME core POSITION_INDEPENDENT_CODE ON" in generated
        assert "add_library(text_lib SHARED" in generated
        assert "target_link_libraries(text_lib PUBLIC core_lib)" in generated
        assert "target_link_libraries(tool PRIVATE core_lib)" in generated
    assert "shared" not in conan.conanfile(manifest, standalone=True, shared=None)


def test_missing_component_files():
    manifest = parse('[package]\nname = "app"\nversion = "1.0"\n[bin.tool]\nmain = "tool.cpp"\n')
    library, tool = manifest.components
    sources = Sources((ComponentSources(library, ("lib/a.cpp",)), ComponentSources(tool)))
    with pytest.raises(CxyError, match=r"bin\.tool: no tool\.cpp"):
        cmake.cmakelists(manifest, sources, {})
