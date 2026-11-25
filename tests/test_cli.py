import subprocess
import sys

import pytest


def cxy(*arguments, cwd):
    return subprocess.run(
        [sys.executable, "-c", "from cxy.cli import main; main()", *arguments],
        cwd=cwd,
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
