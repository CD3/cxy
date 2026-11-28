import json
import subprocess
import sys

import pytest

from cxy.core import importing
from cxy.core import manifest as manifests
from cxy.core.importing import CMakeProject, Findings, Recipe, Requirement, Target


def install_output(*dependencies, **root):
    nodes = {"0": {"dependencies": dict(enumerate(dependencies)), **root}}
    return json.dumps({"graph": {"nodes": nodes}})


def dependency(require, **traits):
    return {"ref": require, "require": require, "direct": True, **traits}


def read(tmp_path, name, text, output):
    (tmp_path / name).write_text(text)
    findings = Findings()
    return importing.read_recipe(tmp_path / name, output, findings), findings


def test_scan_recipe():
    overrides, conditional = importing.scan_recipe(
        "class P:\n"
        "    def requirements(self):\n"
        '        self.requires("fmt/10.2.1")\n'
        '        self.requires("zlib/1.3.1", override=True)\n'
    )
    assert overrides == {"zlib": "1.3.1"}
    assert not conditional
    _, conditional = importing.scan_recipe(
        "class P:\n"
        "    def requirements(self):\n"
        '        if self.settings.os == "Windows":\n'
        '            self.requires("fmt/10.2.1")\n'
    )
    assert conditional


def test_read_conanfile_py(tmp_path):
    output = install_output(
        dependency("fmt/[>=10 <11]"),
        dependency("boost/1.86.0", force=True),
        dependency("gtest/1.17.0", test=True),
        dependency("zlib/1.3.1", direct=False),
        dependency("cmake/4.5.0", build=True),
        dependency("protobuf/5.27.0", build=True),
        default_options={"boost/*:header_only": "True", "zlib/*:shared": "True"},
        name="demo",
        version="1.0",
    )
    recipe, findings = read(tmp_path, "conanfile.py", "", output)
    assert recipe == Recipe(
        name="demo",
        version="1.0",
        requirements=(
            Requirement("fmt", "[>=10 <11]"),
            Requirement("boost", "1.86.0", force=True, options={"header_only": True}),
            Requirement("gtest", "1.17.0", test=True),
        ),
    )
    assert len(findings.problems) == 2
    assert "protobuf/5.27.0 is a tool requirement" in findings.problems[0]
    assert findings.warnings == ["cxy uses its own CMake and Ninja, not cmake/4.5.0"]
    assert "zlib/*:shared=True is not for a direct dependency" in findings.problems[1]


def test_read_conanfile_txt(tmp_path):
    text = "[requires]\nfmt/10.2.1\n\n[options]\nfmt/*:header_only=True  # comment\n"
    recipe, findings = read(
        tmp_path, "conanfile.txt", text, install_output(dependency("fmt/10.2.1"))
    )
    assert recipe.requirements == (Requirement("fmt", "10.2.1", options={"header_only": True}),)
    assert findings == Findings()


def test_overrides_are_not_requirements(tmp_path):
    text = 'def requirements(self):\n    self.requires("fmt/10.2.0", override=True)\n'
    output = install_output(dependency("fmt/10.2.0"))
    recipe, _ = read(tmp_path, "conanfile.py", text, output)
    assert recipe.requirements == ()
    assert recipe.overrides == {"fmt": "10.2.0"}


def write(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


GTEST = "#include <gtest/gtest.h>\nTEST(A, b) {}\n"
RECIPE = Recipe(requirements=(Requirement("gtest", "1.17.0", test=True),))


def propose(root, targets, recipe=RECIPE, **cmake):
    findings = Findings()
    text = importing.propose(
        root, recipe, CMakeProject("Demo", "1.0", tuple(targets), **cmake), findings
    )
    return manifests.parse(text), findings


def test_standard_layout(tmp_path):
    write(
        tmp_path,
        {
            "include/demo/a.hpp": "",
            "lib/a.cpp": "",
            "lib/a_test.cpp": GTEST,
            "src/main.cpp": "",
            "tests/one.cpp": GTEST,
            "tests/two.cpp": GTEST,
        },
    )
    include = ("include",)
    manifest, findings = propose(
        tmp_path,
        [
            Target("demo_lib", "STATIC_LIBRARY", sources=("lib/a.cpp",), includes=include),
            Target("demo", "EXECUTABLE", "demo", ("src/main.cpp",), includes=include),
            Target("a_test", "EXECUTABLE", sources=("lib/a_test.cpp",), includes=include),
            Target("one", "EXECUTABLE", sources=("tests/one.cpp",), includes=include),
            Target("two", "EXECUTABLE", sources=("tests/two.cpp",), includes=include),
            Target("deps", "INTERFACE_LIBRARY"),
        ],
    )
    assert findings.problems == []
    assert manifest.name == "demo" and manifest.version == "1.0"
    assert manifest.layout == manifests.Layout()
    assert manifest.test_separate
    assert [d.name for d in manifest.dev_dependencies] == ["gtest"]
    assert any("does not choose a C++ standard" in w for w in findings.warnings)


def test_headers_beside_sources(tmp_path):
    write(tmp_path, {"src/a.hpp": "", "src/a.cpp": "", "src/main.cpp": "", "tests/a.cpp": GTEST})
    manifest, findings = propose(
        tmp_path,
        [
            Target("core", "STATIC_LIBRARY", sources=("src/a.cpp",), includes=("src",)),
            Target("demo", "EXECUTABLE", "demo", ("src/main.cpp",), includes=("src",)),
            Target("tests", "EXECUTABLE", sources=("tests/a.cpp",), includes=("src",)),
        ],
    )
    assert findings.problems == []
    assert manifest.layout.lib == manifest.layout.include == manifest.layout.src == "src"
    assert not manifest.test_separate


def test_what_cxy_cannot_build(tmp_path):
    write(tmp_path, {"lib/a.cpp": "", "lib/unused.cpp": "", "src/app.cpp": ""})
    _, findings = propose(
        tmp_path,
        [
            Target("a", "STATIC_LIBRARY", sources=("lib/a.cpp",)),
            Target("app", "EXECUTABLE", sources=("src/app.cpp",)),
            Target("vendored", "STATIC_LIBRARY", foreign=("/elsewhere/x.cpp",)),
        ],
        recipe=Recipe(),
    )
    problems = "\n".join(findings.problems)
    assert "vendored is built from files that are generated or outside" in problems
    assert "main() in a file named main.cpp" in problems
    assert "would also compile" in problems and "lib/unused.cpp" in problems


def test_libraries_in_one_directory(tmp_path):
    write(tmp_path, {"lib/a.cpp": "", "lib/b.cpp": ""})
    findings = Findings()
    importing.propose(
        tmp_path,
        Recipe(),
        CMakeProject(
            "Demo",
            "1.0",
            (
                Target("a", "STATIC_LIBRARY", sources=("lib/a.cpp",)),
                Target("b", "STATIC_LIBRARY", sources=("lib/b.cpp",)),
            ),
        ),
        findings,
    )
    assert findings.problems == [
        "cxy cannot describe the project this way: lib.a and lib.b have the same sources directory"
    ]


def test_what_conan_does_not_provide(tmp_path):
    package = tmp_path / "conan" / "fmt"
    output = install_output(
        dependency("fmt/10.2.1"),
        package_folder=None,
    )
    nodes = json.loads(output)
    nodes["graph"]["nodes"]["1"] = {
        "package_folder": str(package),
        "cpp_info": {"root": {"system_libs": ["m"], "defines": ["FMT_SHARED"]}},
    }
    provided = importing.conan_provides(json.dumps(nodes))
    for item in (
        "-lm",
        "m.lib",
        "-DFMT_SHARED",
        f"{package}/lib/libfmt.a",
        f"-L{package}/lib",
        f"-LIBPATH:{package}/lib",
        f"/LIBPATH:{package}/lib",
    ):
        assert provided.provides(item), item
    for item in ("-fopenmp", "-lgomp", "gomp.lib", "/usr/lib/libgomp.so", f"-L{tmp_path}"):
        assert not provided.provides(item), item

    write(tmp_path, {"lib/a.cpp": "", "src/main.cpp": ""})
    inherited = ("-fopenmp", f"{package}/lib/libfmt.a", "/usr/lib/libgomp.so")
    findings = Findings()
    importing.propose(
        tmp_path,
        Recipe(),
        CMakeProject(
            "Demo",
            "1.0",
            (
                Target("a", "STATIC_LIBRARY", sources=("lib/a.cpp",), inherited=inherited),
                Target("demo", "EXECUTABLE", sources=("src/main.cpp",), inherited=inherited),
            ),
        ),
        findings,
        provided,
    )
    assert findings.problems == [
        "2 targets (a, demo) are built with -fopenmp /usr/lib/libgomp.so, which come from "
        "something other than a Conan package, such as a CMake find module; cxy builds only "
        "with Conan packages"
    ]


def test_framework_in_dependencies_moves(tmp_path):
    write(tmp_path, {"tests/a.cpp": GTEST})
    manifest, findings = propose(
        tmp_path,
        [Target("tests", "EXECUTABLE", sources=("tests/a.cpp",))],
        recipe=Recipe(requirements=(Requirement("gtest", "1.17.0"),)),
    )
    assert findings.problems == []
    assert [d.name for d in manifest.dev_dependencies] == ["gtest"]
    assert manifest.dependencies == ()


def test_standard_from_the_project(tmp_path):
    write(tmp_path, {"lib/a.cpp": ""})
    library = Target("a", "STATIC_LIBRARY", sources=("lib/a.cpp",), standard=23)
    manifest, findings = propose(tmp_path, [library], mentions_standard=True, toolchain_standard=17)
    assert manifest.std == 23
    assert not any("standard" in w for w in findings.warnings)
    manifest, findings = propose(tmp_path, [library], toolchain_standard=23)
    assert any("from your Conan profile" in w for w in findings.warnings)


def cxy(*arguments, cwd):
    return subprocess.run(
        [sys.executable, "-c", "from cxy.cli import main; main()", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,  # Never a terminal, so conflicts are not asked about.
    )


CMAKELISTS = """\
cmake_minimum_required(VERSION 3.17)
project(Mixed VERSION 1.2.3 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(fmt REQUIRED)
find_package(Catch2 REQUIRED)
add_library(mixed_core src/greeting.cpp)
target_include_directories(mixed_core PUBLIC src)
target_link_libraries(mixed_core PUBLIC fmt::fmt)
add_executable(mixed src/main.cpp)
target_link_libraries(mixed PRIVATE mixed_core)
target_compile_definitions(mixed PRIVATE GREETING="hi")
include(CTest)
add_executable(mixed_tests tests/greeting_test.cpp)
target_link_libraries(mixed_tests PRIVATE mixed_core Catch2::Catch2WithMain)
target_compile_definitions(mixed_tests PRIVATE EXPECTED="hi!")
add_test(NAME mixed_tests COMMAND mixed_tests)
"""

CONANFILE = """\
[requires]
fmt/10.2.1

[test_requires]
catch2/3.7.1

[options]
fmt/*:shared=False

[generators]
CMakeDeps
CMakeToolchain

[layout]
cmake_layout
"""


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(root).as_posix().startswith("build/")
    }


@pytest.mark.e2e
def test_import(tmp_path):
    write(
        tmp_path,
        {
            "CMakeLists.txt": CMAKELISTS,
            "conanfile.txt": CONANFILE,
            "src/greeting.hpp": "#include <string>\nstd::string greet();\n",
            "src/greeting.cpp": '#include "greeting.hpp"\n#include <fmt/format.h>\n'
            'std::string greet() { return fmt::format("{}!", "hi"); }\n',
            "src/main.cpp": '#include <cstdio>\n#include "greeting.hpp"\n'
            "int main() { std::puts(GREETING); std::puts(greet().c_str()); }\n",
            "tests/greeting_test.cpp": "#include <catch2/catch_test_macros.hpp>\n"
            '#include "greeting.hpp"\nTEST_CASE("greet") { REQUIRE(greet() == EXPECTED); }\n',
        },
    )
    before = snapshot(tmp_path)

    # The test needs a definition of its own, which cxy cannot set.
    result = cxy("import", cwd=tmp_path)
    assert result.returncode == 1, result.stderr
    assert '-DEXPECTED="hi!"' in result.stderr
    assert not (tmp_path / "cxy.toml").exists()
    assert snapshot(tmp_path) == before

    (tmp_path / "tests" / "greeting_test.cpp").write_text(
        "#include <catch2/catch_test_macros.hpp>\n"
        '#include "greeting.hpp"\nTEST_CASE("greet") { REQUIRE(greet() == "hi!"); }\n'
    )
    before = snapshot(tmp_path)
    result = cxy("import", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {
        k: v for k, v in snapshot(tmp_path).items() if k not in ("cxy.toml", "cxy-import.json")
    } == before
    manifest = manifests.load(tmp_path / "cxy.toml")
    assert (manifest.name, manifest.version, manifest.std) == ("mixed", "1.2.3", 20)
    assert manifest.layout.lib == manifest.layout.include == manifest.layout.src == "src"
    assert manifest.dependencies[0].options == {"shared": False}
    assert [d.name for d in manifest.dev_dependencies] == ["catch2"]
    assert manifest.program.defines == ('GREETING="hi"',)

    result = cxy("run", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-2:] == ["hi", "hi!"]

    result = cxy("test", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All tests passed" in result.stdout
    assert cxy("import", cwd=tmp_path).returncode == 1  # cxy.toml exists.


def test_settings_are_carried(tmp_path):
    write(
        tmp_path,
        {
            "include/a.hpp": "#ifdef API\n#endif\n",
            "lib/a.cpp": '#include "a.hpp"\n',
            "src/main.cpp": "",
            "src/cli.cpp": "",
            "tests/a_test.cpp": GTEST + "#ifdef ONLY_TESTS\n#endif\n",
        },
    )
    common = ("EVERYWHERE", "-Wall")
    manifest, findings = propose(
        tmp_path,
        [
            Target(
                "a",
                "SHARED_LIBRARY",
                sources=("lib/a.cpp",),
                includes=("include",),
                definitions=(common[0], "API=1", "BUILDING_A"),
                flags=(common[1], "-O2"),
            ),
            Target(
                "demo",
                "EXECUTABLE",
                "demo",
                ("src/main.cpp", "src/cli.cpp"),
                definitions=(common[0], "API=1", "APP", "UNUSED"),
                flags=(common[1],),
            ),
            Target(
                "tests",
                "EXECUTABLE",
                sources=("tests/a_test.cpp",),
                definitions=(common[0], "API=1", "APP", "ONLY_TESTS"),
                flags=(common[1], "-g3"),
                link_flags=("-Wl,--as-needed",),
            ),
        ],
    )
    # Definitions on every target can go in either; cxy passes the library's on to the others.
    assert manifest.build.defines == ("EVERYWHERE", "API=1")
    assert manifest.build.flags == ("-Wall",)
    assert manifest.library.shared
    assert manifest.library.defines == ("BUILDING_A",) and manifest.library.flags == ("-O2",)
    assert manifest.program.defines == ("APP", "UNUSED")
    problems = "\n".join(findings.problems)
    assert "tests is compiled with -DONLY_TESTS, which cxy cannot set" in problems
    assert "tests is compiled with options cxy cannot set for it: -g3" in problems
    assert "tests is linked with options cxy cannot set for it: -Wl,--as-needed" in problems
    assert len(findings.problems) == 3


def test_link_options(tmp_path):
    write(tmp_path, {"include/a.hpp": "", "lib/a.cpp": "", "src/main.cpp": ""})
    manifest, findings = propose(
        tmp_path,
        [
            Target(
                "a",
                "SHARED_LIBRARY",
                sources=("lib/a.cpp",),
                includes=("include",),
                link_flags=("-Wl,--as-needed",),
            ),
            Target(
                "demo",
                "EXECUTABLE",
                "demo",
                ("src/main.cpp",),
                link_flags=("-Wl,--as-needed", "-static-libstdc++"),
            ),
        ],
    )
    # What everything is linked with goes in [build], and a program's own in [bin].
    assert manifest.build.link_flags == ("-Wl,--as-needed",)
    assert manifest.program.link_flags == ("-static-libstdc++",)
    assert findings.problems == []


def test_settings_cxy_would_add(tmp_path):
    write(
        tmp_path,
        {
            "include/a.hpp": "",
            "lib/a.cpp": '#include "a.hpp"\n',
            "src/main.cpp": "#ifdef FAST\n#endif\n",
        },
    )
    _, findings = propose(
        tmp_path,
        [
            Target("a", "STATIC_LIBRARY", sources=("lib/a.cpp",), definitions=("FAST", "F(x)=x")),
            Target("demo", "EXECUTABLE", "demo", ("src/main.cpp",)),
        ],
    )
    assert findings.problems == [
        "cxy cannot set -DF(x)=x, which is not NAME or NAME=value",
        "cxy would also compile demo with -DFAST, which it uses",
    ]


def test_several_libraries_and_programs(tmp_path):
    write(
        tmp_path,
        {
            "core/core.cpp": "",
            "core/core.hpp": "",
            "include/text/text.hpp": "",
            "text/text.cpp": "",
            "apps/shout/main.cpp": "",
            "apps/shout/loud.cpp": "",
            "tools/count.cpp": "int main() {}\n",
            "tests/all.cpp": GTEST,
        },
    )
    manifest, findings = propose(
        tmp_path,
        [
            Target("core", "STATIC_LIBRARY", sources=("core/core.cpp",), includes=("core",)),
            Target(
                "Text",
                "SHARED_LIBRARY",
                sources=("text/text.cpp",),
                includes=("include", "core"),
                links=("core",),
            ),
            Target(
                "shout",
                "EXECUTABLE",
                "shout",
                ("apps/shout/main.cpp", "apps/shout/loud.cpp"),
                links=("core", "Text"),
            ),
            Target("count", "EXECUTABLE", "count", ("tools/count.cpp",), links=("core",)),
            Target("tests", "EXECUTABLE", sources=("tests/all.cpp",), links=("core", "Text")),
        ],
    )
    assert findings.problems == []
    core, text, shout, count = manifest.components
    assert (core.name, core.directory, core.include, core.uses) == ("core", "core", "core", ())
    assert (text.name, text.include, text.uses, text.shared) == ("text", "include", ("core",), True)
    assert (shout.directory, shout.main, shout.uses) == (
        "apps/shout",
        "apps/shout/main",
        ("core", "text"),
    )
    assert (count.directory, count.main, count.uses) == (None, "tools/count.cpp", ("core",))


MULTI_CMAKELISTS = """\
cmake_minimum_required(VERSION 3.17)
project(Multi VERSION 2.0.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
find_package(Catch2 REQUIRED)
add_library(core STATIC core/core.cpp)
target_include_directories(core PUBLIC core)
add_library(text SHARED text/text.cpp)
set_target_properties(text PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
target_include_directories(text PUBLIC text)
target_link_libraries(text PUBLIC core)
target_compile_definitions(text PUBLIC TEXT_API=1)
add_executable(shout apps/shout/main.cpp apps/shout/loud.cpp)
target_include_directories(shout PRIVATE apps/shout)
target_link_libraries(shout PRIVATE text)
add_executable(count tools/count.cpp)
target_link_libraries(count PRIVATE core)
include(CTest)
add_executable(core_test core/core_test.cpp)
target_link_libraries(core_test PRIVATE core Catch2::Catch2WithMain)
add_executable(all_tests tests/all.cpp apps/shout/loud.cpp)
target_include_directories(all_tests PRIVATE apps/shout)
target_link_libraries(all_tests PRIVATE text Catch2::Catch2WithMain)
"""


@pytest.mark.e2e
def test_import_several_libraries_and_programs(tmp_path):
    from test_cli import COMPONENTS

    files = {name: text for name, text in COMPONENTS.items() if name != "cxy.toml"}
    files["CMakeLists.txt"] = MULTI_CMAKELISTS
    files["conanfile.txt"] = "[test_requires]\ncatch2/3.7.1\n"
    write(tmp_path, files)

    result = cxy("import", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = manifests.load(tmp_path / "cxy.toml")
    assert {(c.kind.value, c.name) for c in manifest.components} == {
        ("library", "core"),
        ("library", "text"),
        ("program", "shout"),
        ("program", "count"),
    }
    run = cxy("run", "--bin", "shout", cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == "ANSWER=42"
    test = cxy("test", cwd=tmp_path)
    assert test.returncode == 0, test.stdout + test.stderr


def test_header_only_libraries(tmp_path):
    write(
        tmp_path,
        {
            "headers/hdr/answer.hpp": "inline int answer() { return HDR_ANSWER; }\n",
            "core/core.cpp": "#include <hdr/answer.hpp>\n",
            "core/core.hpp": "",
            "app/main.cpp": "",
            "tests/all.cpp": GTEST,
        },
    )
    # What an interface library passes on reaches what links it from target_link_libraries.
    received = ("-DHDR_ANSWER=21",)
    manifest, findings = propose(
        tmp_path,
        [
            Target(
                "hdr", "INTERFACE_LIBRARY", includes=("headers",), definitions=("HDR_ANSWER=21",)
            ),
            Target(
                "core",
                "STATIC_LIBRARY",
                sources=("core/core.cpp",),
                includes=("core", "headers"),
                links=("hdr",),
                inherited=received,
            ),
            Target(
                "app",
                "EXECUTABLE",
                "app",
                ("app/main.cpp",),
                includes=("core", "headers"),
                links=("core",),
                inherited=received,
            ),
            Target(
                "tests",
                "EXECUTABLE",
                sources=("tests/all.cpp",),
                links=("core",),
                inherited=received,
            ),
        ],
    )
    assert findings.problems == []
    hdr, core, app = manifest.components
    assert (hdr.name, hdr.directory, hdr.include, hdr.uses) == ("hdr", "headers", "headers", ())
    assert hdr.settings.defines == ("HDR_ANSWER=21",)
    assert (core.directory, core.include, core.uses) == ("core", "core", ("hdr",))
    assert app.uses == ("core",)
    assert manifest.build.defines == ()


def test_header_only_library_alone(tmp_path):
    write(tmp_path, {"headers/demo/a.hpp": "#ifdef DEMO_X\n#endif\n", "tests/one.cpp": GTEST})
    manifest, findings = propose(
        tmp_path,
        [
            Target("demo", "INTERFACE_LIBRARY", includes=("headers",), definitions=("DEMO_X",)),
            Target(
                "one",
                "EXECUTABLE",
                sources=("tests/one.cpp",),
                includes=("headers",),
                links=("demo",),
                inherited=("-DDEMO_X",),
            ),
        ],
    )
    assert findings.problems == []
    assert manifest.layout.include == "headers"
    assert manifest.library.defines == ("DEMO_X",)


def test_what_interface_libraries_cannot_pass_on(tmp_path):
    write(tmp_path, {"headers/a.hpp": "", "lib/a.cpp": "", "src/main.cpp": ""})
    _, findings = propose(
        tmp_path,
        [
            Target("deps", "INTERFACE_LIBRARY", definitions=("USE_X",)),
            Target(
                "hdr",
                "INTERFACE_LIBRARY",
                includes=("headers",),
                flags=("-Wall",),
                unreadable=("$<$<CONFIG:Debug>:DBG>",),
            ),
            Target(
                "a",
                "STATIC_LIBRARY",
                sources=("lib/a.cpp",),
                includes=("headers",),
                links=("hdr", "deps"),
                inherited=("-DUSE_X", "-Wall"),
            ),
            Target("demo", "EXECUTABLE", "demo", ("src/main.cpp",), links=("a",)),
        ],
    )
    assert findings.problems == [
        "hdr passes on what cxy cannot read: $<$<CONFIG:Debug>:DBG>",
        "a is compiled with -Wall from hdr, but cxy passes a library's options on to nothing else",
        "deps passes on -DUSE_X, but has no headers in the project, so cxy has no library for "
        "them to come from",
    ]


def test_interface_libraries_from_the_listing(tmp_path):
    def project_path(text, base=tmp_path):
        path = (base / text).resolve()
        return path.relative_to(tmp_path).as_posix() if path.is_relative_to(tmp_path) else None

    listing = [
        {
            "name": "hdr",
            "type": "INTERFACE_LIBRARY",
            "directory": str(tmp_path / "sub"),
            "links": ["fmt::fmt", "other"],
            "includes": [f"$<BUILD_INTERFACE:{tmp_path}/sub/include>", "$<INSTALL_INTERFACE:x>"],
            "definitions": ["-DA=1", "$<$<CONFIG:Debug>:B>"],
            "options": [],
            "link_options": ["-pthread"],
        },
        {
            "name": "outside",
            "type": "INTERFACE_LIBRARY",
            "directory": "/elsewhere",
            "links": [],
            "includes": [],
            "definitions": [],
            "options": [],
            "link_options": [],
        },
        {"name": "core", "type": "STATIC_LIBRARY", "directory": str(tmp_path), "links": ["hdr"]},
    ]
    core, other, hdr = importing._with_interface_libraries(
        [Target("core", "STATIC_LIBRARY", links=("other",)), Target("other", "STATIC_LIBRARY")],
        listing,
        project_path,
    )
    assert core.links == ("other", "hdr") and other.links == ()
    assert (hdr.name, hdr.kind, hdr.includes) == ("hdr", "INTERFACE_LIBRARY", ("sub/include",))
    assert (hdr.definitions, hdr.link_flags, hdr.links) == (("A=1",), ("-pthread",), ("other",))
    assert hdr.unreadable == ("$<$<CONFIG:Debug>:B>",)


HEADER_ONLY_CMAKELISTS = """\
cmake_minimum_required(VERSION 3.17)
project(Answers VERSION 1.0.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(Catch2 REQUIRED)
add_subdirectory(headers)
add_library(core STATIC core/core.cpp)
target_include_directories(core PUBLIC core)
target_link_libraries(core PUBLIC answers::hdr)
add_executable(app app/main.cpp)
target_link_libraries(app PRIVATE core)
include(CTest)
add_executable(all_tests tests/all.cpp)
target_link_libraries(all_tests PRIVATE core Catch2::Catch2WithMain)
"""

HEADER_ONLY_FILES = {
    "CMakeLists.txt": HEADER_ONLY_CMAKELISTS,
    "headers/CMakeLists.txt": "add_library(hdr INTERFACE)\n"
    "add_library(answers::hdr ALIAS hdr)\n"
    "target_include_directories(hdr INTERFACE\n"
    "  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}> $<INSTALL_INTERFACE:include>)\n"
    "target_compile_definitions(hdr INTERFACE HDR_ANSWER=21)\n",
    "headers/hdr/answer.hpp": "#pragma once\ninline int answer() { return HDR_ANSWER; }\n",
    "core/core.hpp": "#pragma once\nint doubled();\n",
    "core/core.cpp": '#include "core.hpp"\n#include <hdr/answer.hpp>\n'
    "int doubled() { return 2 * answer(); }\n",
    "app/main.cpp": '#include <cstdio>\n#include "core.hpp"\n'
    'int main() { std::printf("%d\\n", doubled()); }\n',
    "tests/all.cpp": "#include <catch2/catch_test_macros.hpp>\n"
    '#include <hdr/answer.hpp>\n#include "core.hpp"\n'
    'TEST_CASE("answers") { REQUIRE(answer() == 21); REQUIRE(doubled() == 42); }\n',
    "conanfile.txt": "[test_requires]\ncatch2/3.7.1\n",
}


@pytest.mark.e2e
def test_import_header_only_library(tmp_path):
    write(tmp_path, HEADER_ONLY_FILES)
    result = cxy("import", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = manifests.load(tmp_path / "cxy.toml")
    components = {c.where: c for c in manifest.components}
    assert set(components) == {"lib.hdr", "lib.core", "bin.app"}
    hdr, core = components["lib.hdr"], components["lib.core"]
    assert (hdr.directory, hdr.settings.defines) == ("headers", ("HDR_ANSWER=21",))
    assert (core.uses, components["bin.app"].uses) == (("hdr",), ("core",))
    run = cxy("run", cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == "42"
    test = cxy("test", cwd=tmp_path)
    assert test.returncode == 0, test.stdout + test.stderr


@pytest.mark.e2e
def test_import_update(tmp_path):
    write(tmp_path, HEADER_ONLY_FILES)
    assert cxy("import", cwd=tmp_path).returncode == 0
    assert (tmp_path / "cxy-import.json").is_file()
    manifest_path = tmp_path / "cxy.toml"

    # You change one setting, and the team another.
    with manifest_path.open("a") as file:
        file.write("\n[test]\nseparate = true  # mine\n")
    cmakelists = tmp_path / "CMakeLists.txt"
    cmakelists.write_text(
        HEADER_ONLY_CMAKELISTS + "target_compile_definitions(core PUBLIC CORE_FAST)\n"
    )
    built = cxy("build", cwd=tmp_path)
    assert built.returncode == 0, built.stdout + built.stderr
    assert "CMakeLists.txt has changed since cxy.toml was imported" in built.stderr
    refused = cxy("import", cwd=tmp_path)
    assert refused.returncode == 1 and "--update" in refused.stderr

    before = manifest_path.read_text()
    dry = cxy("import", "--update", "--dry-run", cwd=tmp_path)
    assert dry.returncode == 0, dry.stderr
    assert 'lib.core.defines: (none) -> ["CORE_FAST"]' in dry.stderr
    assert manifest_path.read_text() == before

    updated = cxy("import", "--update", cwd=tmp_path)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert "Updated lib.core.defines" in updated.stderr
    text = manifest_path.read_text()
    assert "separate = true  # mine" in text
    manifest = manifests.load(manifest_path)
    core = next(c for c in manifest.components if c.name == "core")
    assert core.settings.defines == ("CORE_FAST",) and manifest.test_separate
    built = cxy("build", cwd=tmp_path)
    assert built.returncode == 0 and "has changed" not in built.stderr

    # Both change the same setting.
    manifest_path.write_text(
        text.replace('defines = ["CORE_FAST"]', 'defines = ["CORE_FAST", "MINE"]')
    )
    cmakelists.write_text(
        HEADER_ONLY_CMAKELISTS + "target_compile_definitions(core PUBLIC CORE_FASTER)\n"
    )
    conflicted = cxy("import", "--update", cwd=tmp_path)
    assert conflicted.returncode == 1
    assert "lib.core.defines" in conflicted.stderr and "--resolve" in conflicted.stderr
    assert '["CORE_FAST", "MINE"]' in manifest_path.read_text()
    resolved = cxy("import", "--update", "--resolve", "upstream", cwd=tmp_path)
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    core = next(c for c in manifests.load(manifest_path).components if c.name == "core")
    assert core.settings.defines == ("CORE_FASTER",)
    assert cxy("import", "--update", cwd=tmp_path).stderr.count("Up to date") == 1
