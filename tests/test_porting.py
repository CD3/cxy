import json
import subprocess
import sys

import pytest

from cxy.core import manifest as manifests
from cxy.core import porting
from cxy.core.porting import CMakeProject, Findings, Recipe, Requirement, Target


def install_output(*dependencies, **root):
    nodes = {"0": {"dependencies": dict(enumerate(dependencies)), **root}}
    return json.dumps({"graph": {"nodes": nodes}})


def dependency(require, **traits):
    return {"ref": require, "require": require, "direct": True, **traits}


def read(tmp_path, name, text, output):
    (tmp_path / name).write_text(text)
    findings = Findings()
    return porting.read_recipe(tmp_path / name, output, findings), findings


def test_scan_recipe():
    overrides, conditional = porting.scan_recipe(
        "class P:\n"
        "    def requirements(self):\n"
        '        self.requires("fmt/10.2.1")\n'
        '        self.requires("zlib/1.3.1", override=True)\n'
    )
    assert overrides == {"zlib": "1.3.1"}
    assert not conditional
    _, conditional = porting.scan_recipe(
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
    text = porting.propose(
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
    write(
        tmp_path,
        {
            "lib/a.cpp": '#include "a.hpp"\n',
            "lib/a.hpp": "#ifdef FAST\n#endif\n",
            "lib/b.cpp": "",
            "lib/unused.cpp": "",
            "src/app.cpp": "",
        },
    )
    _, findings = propose(
        tmp_path,
        [
            Target("a", "STATIC_LIBRARY", sources=("lib/a.cpp",), definitions=("FAST", "SLOW=1")),
            Target("b", "STATIC_LIBRARY", sources=("lib/b.cpp",), flags=("-ffast-math",)),
            Target("app", "EXECUTABLE", sources=("src/app.cpp",)),
            Target("vendored", "STATIC_LIBRARY", foreign=("/elsewhere/x.cpp",)),
        ],
        recipe=Recipe(),
    )
    problems = "\n".join(findings.problems)
    assert "vendored is built from files that are generated or outside" in problems
    assert "builds 2 libraries (a, b)" in problems
    assert "main() in a file named main.cpp" in problems
    assert "a is compiled with -DFAST" in problems
    assert "-ffast-math" in problems
    assert "would also compile" in problems and "lib/unused.cpp" in problems
    assert any("-DSLOW=1" in w and "nothing they compile uses it" in w for w in findings.warnings)


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
    provided = porting.conan_provides(json.dumps(nodes))
    for item in ("-lm", "-DFMT_SHARED", f"{package}/lib/libfmt.a", f"-L{package}/lib"):
        assert provided.provides(item), item
    for item in ("-fopenmp", "-lgomp", "/usr/lib/libgomp.so", f"-L{tmp_path}"):
        assert not provided.provides(item), item

    write(tmp_path, {"lib/a.cpp": "", "src/main.cpp": ""})
    inherited = ("-fopenmp", f"{package}/lib/libfmt.a", "/usr/lib/libgomp.so")
    findings = Findings()
    porting.propose(
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
def test_port(tmp_path):
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
            '#include "greeting.hpp"\nTEST_CASE("greet") { REQUIRE(greet() == "hi!"); }\n',
        },
    )
    before = snapshot(tmp_path)

    # main.cpp needs a definition that cxy cannot set.
    result = cxy("port", cwd=tmp_path)
    assert result.returncode == 1, result.stderr
    assert '-DGREETING="hi"' in result.stderr
    assert not (tmp_path / "cxy.toml").exists()
    assert snapshot(tmp_path) == before

    (tmp_path / "src" / "main.cpp").write_text(
        '#include <cstdio>\n#include "greeting.hpp"\nint main() { std::puts(greet().c_str()); }\n'
    )
    before = snapshot(tmp_path)
    result = cxy("port", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {k: v for k, v in snapshot(tmp_path).items() if k != "cxy.toml"} == before
    manifest = manifests.load(tmp_path / "cxy.toml")
    assert (manifest.name, manifest.version, manifest.std) == ("mixed", "1.2.3", 20)
    assert manifest.layout.lib == manifest.layout.include == manifest.layout.src == "src"
    assert manifest.dependencies[0].options == {"shared": False}
    assert [d.name for d in manifest.dev_dependencies] == ["catch2"]

    result = cxy("test", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All tests passed" in result.stdout
    assert cxy("port", cwd=tmp_path).returncode == 1  # cxy.toml exists.
