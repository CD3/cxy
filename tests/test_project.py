import pytest

from cxy.core.errors import CxyError
from cxy.core.project import Project

PACKAGE = '[package]\nname = "app"\nversion = "1.0"\n'


@pytest.fixture
def make(tmp_path):
    def _make(*files, layout=""):
        (tmp_path / "cxy.toml").write_text(PACKAGE + layout)
        for name in files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return Project.find(tmp_path)

    return _make


def test_default_layout_with_tests_beside_sources(make):
    sources = make(
        "lib/a.cpp",
        "lib/a_test.cpp",
        "include/app/a.hpp",
        "include/app/a.test.cpp",
        "src/main.cpp",
        "src/cli.cpp",
        "src/cli_test.cpp",
        "tests/helpers.cpp",
        "build/cxy/x.cpp",
    ).sources()
    library, program = sources.components
    assert library.sources == ("lib/a.cpp",)
    assert (program.sources, program.main) == (("src/cli.cpp",), "src/main.cpp")
    assert sources.unit_tests == (
        "include/app/a.test.cpp",
        "lib/a_test.cpp",
        "src/cli_test.cpp",
    )
    assert sources.tests == ("tests/helpers.cpp",)
    assert library.has_headers


def test_everything_in_src(make):
    layout = '[layout]\nlib = "src"\ninclude = "src"\ntests = "src/tests"\n'
    sources = make("src/a.cpp", "src/main.cpp", "src/tests/t.cpp", layout=layout).sources()
    library, program = sources.components
    assert (library.sources, program.main, sources.tests) == (
        ("src/a.cpp",),
        "src/main.cpp",
        ("src/tests/t.cpp",),
    )


def test_find_walks_up(make, tmp_path):
    make("lib/deep/a.cpp")
    assert Project.find(tmp_path / "lib" / "deep").root == tmp_path


def test_two_mains(make):
    with pytest.raises(CxyError, match="main file"):
        make("src/main.cpp", "src/main.cc").sources()
