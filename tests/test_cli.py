import json
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
