import pytest

from cxy.core import formatting, manifest, scaffold
from cxy.core.workspace import Workspace


def test_new_project_is_formatted(tmp_path):
    scaffold.new(tmp_path / "demo")
    assert formatting.fmt(Workspace.find(tmp_path / "demo"), check=True) == 0


def test_init_does_not_impose_a_style_on_existing_code(tmp_path):
    (tmp_path / "src").mkdir()
    scaffold.init(tmp_path, name="demo")
    assert not (tmp_path / ".clang-format").exists()


def dev_dependencies(root):
    return [d.name for d in manifest.load(root / "cxy.toml").dev_dependencies]


def test_new_project_uses_catch2(tmp_path):
    scaffold.new(tmp_path / "demo")
    assert dev_dependencies(tmp_path / "demo") == ["catch2"]


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"src/main.cpp": "int main() {}"}, []),
        ({"tests/a.cpp": "#include <gtest/gtest.h>\n"}, ["gtest"]),
        ({"tests/a.cpp": "#include <gmock/gmock.h>\n"}, ["gtest"]),
        ({"lib/a_test.cpp": "#include <catch2/catch_all.hpp>\n"}, ["catch2"]),
        (
            {"tests/a.cpp": '#include "all.hpp"\n', "tests/all.hpp": "#include <gtest/gtest.h>\n"},
            ["gtest"],
        ),
        (
            {
                "tests/a.cpp": "#include <gtest/gtest.h>\n",
                "tests/b.cpp": "#include <catch2/catch_all.hpp>\n",
            },
            ["catch2", "gtest"],
        ),
        # Not a test.
        ({"lib/a.cpp": "#include <gtest/gtest.h>\n"}, []),
        # Not cxy's business until the project is built.
        (
            {
                "src/main.cpp": "",
                "src/main.cc": "",
                "tests/a.cpp": "#include <gtest/gtest.h>\n",
            },
            ["gtest"],
        ),
    ],
)
def test_init_uses_the_frameworks_of_existing_tests(tmp_path, files, expected):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    scaffold.init(tmp_path, name="demo")
    assert dev_dependencies(tmp_path) == expected
