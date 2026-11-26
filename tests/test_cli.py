import json
import os
import re
import subprocess
import sys

import pytest


def cxy(*arguments, cwd, env=None):
    return subprocess.run(
        [sys.executable, "-c", "from cxy.cli import main; main()", *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def test_error_outside_project(tmp_path):
    result = cxy("build", cwd=tmp_path)
    assert result.returncode == 1
    assert result.stderr.startswith("error: no cxy.toml")


def test_new_refuses_existing(tmp_path):
    assert cxy("new", "demo", cwd=tmp_path).returncode == 0
    assert cxy("new", "demo", cwd=tmp_path).returncode == 1


def test_clean_refuses_foreign_directory(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    (tmp_path / "demo" / "build" / "cxy").mkdir(parents=True)
    assert cxy("clean", cwd=tmp_path / "demo").returncode == 1
    assert (tmp_path / "demo" / "build" / "cxy").is_dir()


@pytest.mark.e2e
def test_new_build_run_test(tmp_path):
    assert cxy("new", "demo", cwd=tmp_path).returncode == 0
    project = tmp_path / "demo"
    (project / "src" / "shout.cpp").write_text("int shout() { return 42; }\n")
    (project / "src" / "shout_test.cpp").write_text(
        "#include <catch2/catch_test_macros.hpp>\n"
        "int shout();\n"
        'TEST_CASE("shout") { REQUIRE(shout() == 42); }\n'
    )
    build = cxy("build", cwd=project)
    assert build.returncode == 0, build.stdout + build.stderr

    run = cxy("run", "--", "--flag", cwd=project)
    assert "Hello, --flag!" in run.stdout
    assert "Resolving" not in run.stderr and "Configuring" not in run.stderr

    test = cxy("test", "--", "--reporter", "compact", cwd=project)
    assert test.returncode == 0, test.stdout + test.stderr
    assert test.stdout.count("1 test case") == 2
    filtered = cxy("test", "--", "shout", cwd=project)
    assert filtered.returncode == 0, filtered.stdout + filtered.stderr
    assert cxy("test", "--", "no such test", cwd=project).returncode != 0

    (project / "src" / "shout.cpp").write_text("int shout() { return 0; }\n")
    assert cxy("test", cwd=project).returncode != 0
    assert cxy("clean", cwd=project).returncode == 0
    assert not (project / "build" / "cxy").exists()


@pytest.mark.e2e
def test_isolated_test_ignores_broken_code(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "tests" / "greeting_test.cpp").rename(project / "lib" / "greeting_test.cpp")
    (project / "lib" / "broken.cpp").write_text("int broken() { return renamed(); }\n")
    assert cxy("test", "--test", "greeting", cwd=project).returncode == 1
    result = cxy("test", "--test", "greeting", "--isolated", cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.e2e
def test_definitions_and_shared_library(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    with (project / "cxy.toml").open("a", encoding="utf-8") as file:
        file.write(
            """
[build]
defines = ["EVERYWHERE"]

[lib]
shared = true
defines = ['GREETING="Howdy"']

[bin]
defines = ['PUNCTUATION="?"']
"""
        )
    required = "#ifndef EVERYWHERE\n#error EVERYWHERE is not defined\n#endif\n"
    (project / "lib" / "greeting.cpp").write_text(
        f'#include "demo/greeting.hpp"\n{required}'
        "std::string demo::greet(std::string_view name) {\n"
        '    return GREETING ", " + std::string(name) + "!";\n'
        "}\n"
    )
    (project / "src" / "main.cpp").write_text(
        f'#include <iostream>\n#include "demo/greeting.hpp"\n{required}'
        "int main() { std::cout << demo::greet(\"world\") << PUNCTUATION << '\\n'; }\n"
    )
    # The library's definitions reach whatever uses it, tests included.
    (project / "tests" / "greeting_test.cpp").unlink()
    (project / "lib" / "greeting_test.cpp").write_text(
        '#include "demo/greeting.hpp"\n#include <catch2/catch_test_macros.hpp>\n'
        'TEST_CASE("greet") { REQUIRE(demo::greet("cxy") == GREETING ", cxy!"); }\n'
    )

    run = cxy("run", cwd=project)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Howdy, world!?" in run.stdout
    names = {"libdemo.so", "libdemo.dylib", "demo.dll"}
    assert any(path.name in names for path in (project / "build" / "cxy").rglob("*demo*"))

    test = cxy("test", cwd=project)
    assert test.returncode == 0, test.stdout + test.stderr
    isolated = cxy("test", "--test", "greeting", "--isolated", cwd=project)
    assert isolated.returncode == 0, isolated.stdout + isolated.stderr


@pytest.mark.e2e
def test_export_builds_without_cxy(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    result = cxy("export", cwd=project)
    assert result.returncode == 0, result.stderr
    assert cxy("export", cwd=project).returncode == 0

    def tool(*command):
        done = subprocess.run(command, cwd=project, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    assert (project / "conan.lock").is_file()
    (project / "cxy.toml").rename(tmp_path / "cxy.toml")
    generator = "tools.cmake.cmaketoolchain:generator=Ninja"
    settings = ("--build=missing", "-s", "compiler.cppstd=20", "-c", generator)
    tool("conan", "install", ".", *settings)
    tool("cmake", "--preset", "conan-release")
    tool("cmake", "--build", "--preset", "conan-release")
    assert "100% tests passed" in tool("ctest", "--preset", "conan-release")

    (tmp_path / "cxy.toml").rename(project / "cxy.toml")
    (project / "CMakeLists.txt").write_text("# edited\n")
    assert cxy("export", cwd=project).returncode == 1
    assert (project / "CMakeLists.txt").read_text() == "# edited\n"
    assert cxy("export", "--force", cwd=project).returncode == 0

    # A file that cxy wrote and nobody edited needs no --force, even when it changes.
    (project / "lib" / "extra.cpp").write_text("int extra() { return 1; }\n")
    assert cxy("export", cwd=project).returncode == 0
    assert "extra.cpp" in (project / "CMakeLists.txt").read_text()


@pytest.mark.e2e
def test_lock_follows_the_manifest(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    package = '[package]\nname = "demo"\nversion = "1"\n[dev-dependencies]\ncatch2 = "3.7.1"\n'

    def use(text):
        (project / "cxy.toml").write_text(package + text)

    def locked():
        conan = json.loads((project / "cxy.lock").read_text())["conan"]
        return {ref.partition("#")[0] for ref in conan["requires"] + conan["build_requires"]}

    def ok(*arguments):
        result = cxy(*arguments, cwd=project)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stderr

    spdlog = '[dependencies]\nspdlog = "1.14.1"\n'
    use(spdlog)
    assert cxy("build", "--locked", cwd=project).returncode == 1
    assert cxy("lock", "--check", cwd=project).returncode == 1
    ok("lock")
    assert {"spdlog/1.14.1", "fmt/10.2.1"} <= locked()
    ok("lock", "--check")
    ok("build", "--locked")

    # A lock made elsewhere may lack something that is needed here.
    path = project / "cxy.lock"
    complete = path.read_text()
    data = json.loads(complete)
    data["conan"]["requires"] = [r for r in data["conan"]["requires"] if "fmt" not in r]
    path.write_text(json.dumps(data))
    for command in ("lock", "--check"), ("build", "--locked"):
        result = cxy(*command, cwd=project)
        assert result.returncode == 1 and "not in lockfile" in result.stderr
    ok("lock")
    assert path.read_text() == complete

    # Conan's own lockfile would ignore each of these changes to the override.
    for version in ("10.2.0", "10.1.1"):
        use(spdlog + f'[overrides]\nfmt = "{version}"\n')
        assert cxy("build", "--locked", cwd=project).returncode == 1
        ok("lock")
        assert {ref for ref in locked() if ref.startswith("fmt/")} == {f"fmt/{version}"}
    ok("lock", "--check")
    use(spdlog)
    ok("build")
    assert {ref for ref in locked() if ref.startswith("fmt/")} == {"fmt/10.2.1"}

    # A range keeps its locked version until it is upgraded.
    use('[dependencies]\nfmt = "10.2.0"\n')
    ok("lock")
    use('[dependencies]\nfmt = "[>=10 <11]"\n')
    ok("lock")
    assert "fmt/10.2.0" in locked()
    assert "Unchanged" in ok("lock")
    assert "changed fmt 10.2.0 -> 10.2.1" in ok("lock", "--upgrade-package", "fmt")
    ok("lock", "--upgrade")
    assert not any(ref.startswith("spdlog") for ref in locked())
    before = (project / "cxy.lock").read_text()
    ok("build", "--release")
    assert (project / "cxy.lock").read_text() == before
    assert "Resolving" not in ok("build", "--release")


@pytest.mark.e2e
def test_add_and_remove(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    toml, lock = project / "cxy.toml", project / "cxy.lock"
    toml.write_text("# keep me\n" + toml.read_text())

    def ok(*arguments):
        result = cxy(*arguments, cwd=project)
        assert result.returncode == 0, result.stdout + result.stderr

    ok("add", "spdlog@1.14.1")
    assert "spdlog/1.14.1" in lock.read_text()

    # spdlog needs fmt 10.2.1, so this cannot be resolved and nothing may change.
    before = toml.read_text(), lock.read_text()
    assert cxy("add", "fmt@10.2.0", cwd=project).returncode == 1
    assert cxy("add", "zlib", "no-such-package-xyz", cwd=project).returncode == 1
    assert (toml.read_text(), lock.read_text()) == before

    ok("add", "fmt/10.2.0", "--force-version")
    assert 'fmt = {version = "10.2.0", force = true}' in toml.read_text()
    ok("lock", "--check")
    ok("remove", "fmt", "spdlog")
    assert "spdlog" not in toml.read_text() + lock.read_text()
    ok("add", "zlib")
    assert toml.read_text().startswith("# keep me\n")
    ok("build", "--locked")


@pytest.mark.e2e
def test_add_and_remove_override(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "src" / "main.cpp").write_text(
        "#include <fmt/format.h>\n#include <iostream>\nint main() { std::cout << FMT_VERSION; }\n"
    )

    def ok(*arguments):
        result = cxy(*arguments, cwd=project)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    ok("add", "spdlog@1.14.1")
    assert ok("run").stdout.endswith("100201")
    for version, number in ("10.2.0", "100200"), ("10.1.1", "100101"):
        assert "Warning" not in ok("add", "--override", f"fmt@{version}").stderr
        assert ok("run").stdout.endswith(number)
    ok("build", "--locked")
    assert "from overrides" in ok("remove", "fmt").stderr
    assert ok("run").stdout.endswith("100201")
    assert "nothing depends on zlib" in ok("add", "--override", "zlib@1.3.1").stderr

    before = (project / "cxy.toml").read_text()
    for bad in ("spdlog@1.15.0",), ("--dev", "fmt@10.2.0"), ("fmt@10.2.0", "-o", "shared=true"):
        assert cxy("add", "--override", *bad, cwd=project).returncode == 1
    assert (project / "cxy.toml").read_text() == before


@pytest.mark.e2e
def test_unknown_options_are_errors(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    toml = project / "cxy.toml"
    original = toml.read_text()

    result = cxy("add", "zlib@1.3.1", "-o", "shard=true", cwd=project)
    assert result.returncode == 1 and "did you mean 'shared'?" in result.stderr
    assert toml.read_text() == original

    def use(options):
        entry = f'fmt = {{ version = "10.2.1", options = {{ {options} }} }}'
        toml.write_text(original.replace("[dependencies]\n", f"[dependencies]\n{entry}\n"))

    use("header_onyl = true")
    for command in ["build"], ["lock"], ["export"]:
        result = cxy(*command, cwd=project)
        assert result.returncode == 1 and "did you mean 'header_only'?" in result.stderr

    # header_only makes the recipe remove "shared", which is still a valid name.
    use("header_only = true, shared = true")
    for command in ["lock"], ["lock", "--check"], ["build", "--locked"]:
        result = cxy(*command, cwd=project)
        assert result.returncode == 0, result.stdout + result.stderr


GTEST_PROJECT = """\
[package]
name = "demo"
version = "1"
[dev-dependencies]
gtest = "1.17.0"
"""
GTEST_FILE = """\
#include <gmock/gmock.h>
#include <gtest/gtest.h>

struct Base {
    virtual ~Base() = default;
    virtual int get() = 0;
};
struct Mock : Base {
    MOCK_METHOD(int, get, (), (override));
};
TEST(Mocks, work) {
    Mock mock;
    EXPECT_CALL(mock, get()).WillOnce(testing::Return(3));
    EXPECT_EQ(mock.get(), 3);
}
"""
CATCH2_FILE = """\
#include <catch2/catch_test_macros.hpp>
TEST_CASE("catch works") { REQUIRE(true); }
"""


def ctest(project):
    (project / "cxy.toml").rename(project / "cxy.toml.off")
    generator = "tools.cmake.cmaketoolchain:generator=Ninja"
    settings = ("--build=missing", "-s", "compiler.cppstd=20", "-s", "build_type=Debug")
    for command in (
        ("conan", "install", ".", *settings, "-c", generator),
        ("cmake", "--preset", "conan-debug"),
        ("cmake", "--build", "--preset", "conan-debug"),
        ("ctest", "--preset", "conan-debug"),
    ):
        done = subprocess.run(command, cwd=project, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
    (project / "cxy.toml.off").rename(project / "cxy.toml")
    return done.stdout


@pytest.mark.e2e
def test_gtest(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "cxy.toml").write_text(GTEST_PROJECT)
    (project / "tests" / "greeting_test.cpp").write_text(GTEST_FILE)
    (project / "lib" / "greeting_test.cpp").write_text(
        '#include "demo/greeting.hpp"\n#include <gtest/gtest.h>\n'
        'TEST(Greeting, name) { EXPECT_EQ(demo::greet("a"), "Hello, a!"); }\n'
    )

    def test(*arguments):
        return cxy("test", *arguments, cwd=project)

    result = test()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("[  PASSED  ] 1 test.") == 2
    assert test("--test", "greeting", "--isolated").returncode == 0
    result = test("--filter", "Mocks")
    assert result.returncode == 0 and "Mocks.work" in result.stdout
    # gtest itself calls this a success.
    for nothing in ("--filter", "nope"), ("--", "--gtest_filter=nope"):
        result = test(*nothing)
        assert result.returncode == 2 and "no tests matched" in result.stderr

    assert cxy("export", cwd=project).returncode == 0
    # The wording depends on the version of CTest.
    assert re.search(r"100% tests passed.* out of 2\n", ctest(project))

    # A test failure in either program must be noticed.
    (project / "tests" / "greeting_test.cpp").write_text(
        GTEST_FILE.replace("mock.get(), 3", "mock.get(), 4")
    )
    assert test().returncode == 1


@pytest.mark.e2e
def test_gtest_with_its_own_main(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "cxy.toml").write_text(GTEST_PROJECT)
    (project / "tests" / "greeting_test.cpp").write_text(GTEST_FILE)
    (project / "tests" / "main.cpp").write_text(
        "#include <gtest/gtest.h>\n#include <iostream>\n"
        "int main(int argc, char** argv) {\n"
        '    std::cout << "custom main" << std::endl;\n'
        "    testing::InitGoogleTest(&argc, argv);\n"
        "    return RUN_ALL_TESTS();\n}\n"
    )
    result = cxy("test", cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "custom main" in result.stdout and "[  PASSED  ] 1 test." in result.stdout


@pytest.mark.e2e
def test_both_frameworks(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "cxy.toml").write_text(GTEST_PROJECT + 'catch2 = "3.7.1"\n')
    (project / "tests" / "greeting_test.cpp").write_text(GTEST_FILE)
    (project / "tests" / "new_test.cpp").write_text(CATCH2_FILE)
    (project / "lib" / "unit_test.cpp").write_text(CATCH2_FILE)

    def test(*arguments):
        return cxy("test", *arguments, cwd=project)

    result = test()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("All tests passed") == 2
    assert result.stdout.count("[  PASSED  ] 1 test.") == 1
    assert test("--filter", "work").returncode == 0
    assert test("--filter", "nope").returncode == 2
    result = test("--", "--help")
    assert result.returncode == 1 and "different arguments" in result.stderr
    assert test("--test", "demo_tests_gtest", "--", "--gtest_brief=1").returncode == 0

    for name, old, new in ("greeting", "get(), 3", "get(), 4"), ("new", "true", "false"):
        path = project / "tests" / f"{name}_test.cpp"
        text = path.read_text()
        path.write_text(text.replace(old, new))
        assert test().returncode != 0
        path.write_text(text)

    assert cxy("export", cwd=project).returncode == 0
    # The wording depends on the version of CTest.
    assert re.search(r"100% tests passed.* out of 3\n", ctest(project))


@pytest.mark.e2e
@pytest.mark.parametrize("compiled", [True, False])
def test_main_in_a_dependency_does_not_replace_the_frameworks(tmp_path, compiled):
    cxy("new", "demo", "--lib", cwd=tmp_path)
    project = tmp_path / "demo"
    if not compiled:
        (project / "lib" / "greeting.cpp").unlink()
    # As an ordinary dependency, Catch2 is just a library that has a main(), like some of Boost's.
    (project / "cxy.toml").write_text(GTEST_PROJECT + '[dependencies]\ncatch2 = "3.7.1"\n')
    (project / "tests" / "greeting_test.cpp").write_text(GTEST_FILE)
    result = cxy("test", cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[  PASSED  ] 1 test." in result.stdout


@pytest.mark.e2e
def test_separate_programs(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    tests = project / "tests"
    (tests / "greeting_test.cpp").unlink()
    # Both define Same.name, so they cannot be in one program. Both need the helper.
    for name in "one", "two":
        (tests / f"{name}.cpp").write_text(
            "#include <gtest/gtest.h>\nint helper();\n"
            "TEST(Same, name) { EXPECT_EQ(helper(), 1); }\n"
        )
    (tests / "helper.cpp").write_text("int helper() { return 1; }\n")
    (project / "cxy.toml").write_text(GTEST_PROJECT)
    assert cxy("test", cwd=project).returncode == 1
    (project / "cxy.toml").write_text(GTEST_PROJECT + "[test]\nseparate = true\n")

    def ok(*arguments):
        result = cxy("test", *arguments, cwd=project)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.count("[  PASSED  ] 1 test.")

    assert ok() == 2
    assert ok("--test", "tests") == 2
    assert ok("--test", "one") == 1
    assert ok("--test", "tests/two.cpp") == 1

    assert cxy("export", cwd=project).returncode == 0
    assert re.search(r"100% tests passed.* out of 2\n", ctest(project))

    (project / "lib" / "broken.cpp").write_text("int broken() { return renamed(); }\n")
    assert cxy("test", "--test", "one", cwd=project).returncode == 1
    assert ok("--test", "one", "--isolated") == 1


@pytest.mark.e2e
@pytest.mark.parametrize("framework", ["catch2", "gtest"])
def test_working_directory(tmp_path, framework):
    cxy("new", "demo", "--lib", cwd=tmp_path)
    project = tmp_path / "demo"
    body = '{ std::ofstream("out.txt") << 1; }\n'
    if framework == "gtest":
        manifest, test = GTEST_PROJECT, "#include <gtest/gtest.h>\nTEST(Writes, file) " + body
    else:
        manifest = GTEST_PROJECT.replace('gtest = "1.17.0"', 'catch2 = "3.7.1"')
        test = '#include <catch2/catch_test_macros.hpp>\nTEST_CASE("w") ' + body
    (project / "tests" / "greeting_test.cpp").write_text("#include <fstream>\n" + test)
    (project / "tests" / "data").mkdir()
    toml = project / "cxy.toml"
    toml.write_text(manifest)

    def written():
        paths = list(project.rglob("out.txt"))
        for path in paths:
            path.unlink()
        return [path.parent.relative_to(project).as_posix() for path in paths]

    def ctest_here():
        # Discovery happens when CTest runs.
        command = ["ctest", "--test-dir", "build/cxy/debug"]
        done = subprocess.run(command, cwd=project, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr

    for setting, expected in (
        ("", "build/cxy/debug"),
        ('[test]\nworking-directory = "tests/data"\n', "tests/data"),
        ('[test]\nworking-directory = "."\n', "."),
    ):
        toml.write_text(manifest + setting)
        for start in project, project / "lib":
            result = cxy("test", cwd=start)
            assert result.returncode == 0, result.stdout + result.stderr
            assert written() == [expected]
        ctest_here()
        assert written() == [expected]

    assert cxy("export", cwd=project).returncode == 0
    ctest(project)
    assert written() == ["."]

    toml.write_text(manifest + '[test]\nworking-directory = "missing"\n')
    result = cxy("test", cwd=project)
    assert result.returncode == 1 and "not a directory" in result.stderr


@pytest.mark.e2e
def test_gtest_output_is_kept(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "cxy.toml").write_text(GTEST_PROJECT)
    (project / "tests" / "greeting_test.cpp").write_text(GTEST_FILE)
    output = tmp_path / "out.json"
    assert cxy("test", "--", f"--gtest_output=json:{output}", cwd=project).returncode == 0
    # A list of tests has no results.
    assert json.loads(output.read_text())["failures"] == 0


@pytest.mark.e2e
def test_ctest(tmp_path):
    cxy("new", "demo", cwd=tmp_path)
    project = tmp_path / "demo"
    (project / "cxy.toml").write_text(GTEST_PROJECT + 'catch2 = "3.7.1"\n')
    (project / "tests" / "greeting_test.cpp").write_text(GTEST_FILE)
    (project / "lib" / "unit_test.cpp").write_text(
        CATCH2_FILE + 'TEST_CASE("crash") { if (std::getenv("DEMO_CRASH")) std::_Exit(3); }\n'
        'TEST_CASE("after") {}\n'
    )
    text = (project / "lib" / "unit_test.cpp").read_text()
    (project / "lib" / "unit_test.cpp").write_text("#include <cstdlib>\n" + text)

    def test(*arguments, **kwargs):
        return cxy("test", "--ctest", *arguments, cwd=project, **kwargs)

    def passed(*arguments):
        result = test(*arguments)
        assert result.returncode == 0, result.stdout + result.stderr
        match = re.search(r"100% tests passed.* out of (\d+)\n", result.stdout)
        assert match, result.stdout
        return int(match[1])

    # Nothing else has been built yet.
    assert passed("--test", "unit") == 3
    assert passed() == 4
    assert passed("--jobs", "4") == 4
    assert passed("--filter", "work") == 2
    assert passed("--test", "unit", "--filter", "catch") == 1
    # Arguments go to CTest, so it does not matter that there are two frameworks.
    assert passed("--", "--exclude-regex", "crash") == 3
    assert test("--filter", "nope").returncode != 0
    result = test("--test", "unit", "--isolated")
    assert result.returncode == 1 and "cannot be combined" in result.stderr

    # A crash loses only that test, which can then be run again. Not abort(), which shows a
    # dialog in a Windows debug build, so the test would wait for its timeout.
    environment = {**os.environ, "DEMO_CRASH": "1"}
    result = test(env=environment)
    assert result.returncode != 0
    assert re.search(r"1 tests? failed out of 4\n", result.stdout)
    assert passed("--", "--rerun-failed") == 1
