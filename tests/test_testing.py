import pytest

from cxy.core import cmake, frameworks, testing
from cxy.core.conan import CMakeInfo
from cxy.core.errors import CxyError
from cxy.core.manifest import Dependency, parse
from cxy.core.project import Project, Sources
from cxy.core.testing import plan_programs

BOTH = 'catch2 = "3.7.1"\ngtest = "1.17.0"\n'


def manifest(dev=BOTH, extra=""):
    return parse(f'[package]\nname = "app"\nversion = "1"\n[dev-dependencies]\n{dev}{extra}')


def plan(sources, classes=None, **kwargs):
    classes = classes or {}
    programs = plan_programs(manifest(**kwargs), sources, lambda s: classes.get(s, set()))
    return {p.target: (p.framework.package, p.sources) for p in programs}


TESTS = Sources(unit_tests=("lib/a_test.cpp",), tests=("tests/b.cpp", "tests/c.cpp"))


@pytest.mark.parametrize("name", ["catch2", "gtest"])
def test_one_framework_needs_no_classification(name):
    def fail(_):
        raise AssertionError

    programs = plan_programs(manifest(f'{name} = "1"\n'), TESTS, fail)
    assert [p.target for p in programs] == ["app_lib_a_test", "app_tests"]
    assert {p.framework.package for p in programs} == {name}


def test_both_frameworks():
    classes = {
        "lib/a_test.cpp": {"gtest"},
        "tests/b.cpp": {"catch2"},
        "tests/c.cpp": {"gtest"},
    }
    sources = Sources(unit_tests=TESTS.unit_tests, tests=(*TESTS.tests, "tests/helper.cpp"))
    assert plan(sources, classes) == {
        "app_lib_a_test": ("gtest", ("lib/a_test.cpp",)),
        "app_tests_catch2": ("catch2", ("tests/b.cpp", "tests/helper.cpp")),
        "app_tests_gtest": ("gtest", ("tests/c.cpp", "tests/helper.cpp")),
    }


def test_both_available_but_one_used_in_tests():
    classes = {"tests/b.cpp": {"gtest"}}
    assert plan(Sources(tests=TESTS.tests), classes) == {"app_tests": ("gtest", TESTS.tests)}


def test_undecided():
    for sources in Sources(unit_tests=TESTS.unit_tests), Sources(tests=TESTS.tests):
        with pytest.raises(CxyError, match="cannot tell"):
            plan(sources)
        (program,) = plan(sources, extra='[test]\nframework = "gtest"\n').values()
        assert program[0] == "gtest"


def test_including_both_is_always_an_error():
    classes = {"lib/a_test.cpp": {"gtest", "catch2"}, "tests/b.cpp": {"gtest", "catch2"}}
    for sources in Sources(unit_tests=TESTS.unit_tests), Sources(tests=TESTS.tests):
        with pytest.raises(CxyError, match="includes both catch2 and gtest"):
            plan(sources, classes, extra='[test]\nframework = "gtest"\n')


def test_missing_framework():
    assert plan(Sources(lib=("a.cpp",)), dev="") == {}
    with pytest.raises(CxyError, match="add catch2 or gtest"):
        plan(TESTS, dev="")
    with pytest.raises(CxyError, match="not in"):
        plan(TESTS, dev='catch2 = "3"\n', extra='[test]\nframework = "gtest"\n')
    with pytest.raises(CxyError, match="must be one of"):
        manifest(extra='[test]\nframework = "boost"\n')


def test_detect_follows_project_headers(tmp_path):
    files = {
        "cxy.toml": '[package]\nname = "app"\nversion = "1"\n',
        "lib/direct_test.cpp": "#include <gmock/gmock.h>\n",
        "lib/indirect_test.cpp": '#include "support/all.hpp"\n',
        "lib/support/all.hpp": '#include "more.hpp"\n',
        "lib/support/more.hpp": '#include "all.hpp"\n#  include "catch2/catch_all.hpp"\n',
        "lib/none_test.cpp": "// #include <gtest/gtest.h>\n#include <vector>\n",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    project = Project.find(tmp_path)
    assert testing.detect(project, "lib/direct_test.cpp") == {"gtest"}
    assert testing.detect(project, "lib/indirect_test.cpp") == {"catch2"}
    assert testing.detect(project, "lib/none_test.cpp") == set()


def test_gtest_links():
    def links(*components):
        info = CMakeInfo("GTest", "gtest::gtest", frozenset(f"GTest::{c}" for c in components))
        return frameworks.GTEST.links(Dependency("gtest", "1"), {"gtest": info})

    assert links("gtest", "gtest_main", "gmock", "gmock_main") == ("GTest::gmock_main",)
    assert links("gtest", "gtest_main") == ("GTest::gtest_main",)
    assert links("gtest", "gmock") == ("GTest::gmock",)
    assert links("gtest") == ("GTest::gtest",)
    custom = Dependency("gtest", "1", cmake_targets=("mine",))
    assert frameworks.GTEST.links(custom, {}) == ("mine",)


def test_cmake_for_both():
    classes = {"tests/b.cpp": {"catch2"}, "tests/c.cpp": {"gtest"}}
    sources = Sources(lib=("lib/a.cpp",), tests=TESTS.tests)
    both = manifest()
    programs = plan_programs(both, sources, classes.__getitem__)
    text = cmake.cmakelists(both, sources, {}, programs=programs)
    assert "target_link_libraries(app_tests_catch2 PRIVATE Catch2::Catch2WithMain app_lib)" in text
    assert "target_link_libraries(app_tests_gtest PRIVATE GTest::gtest_main app_lib)" in text
    assert "include(Catch)" in text and "include(GoogleTest)" in text
    assert "catch_discover_tests(\n" in text and "gtest_discover_tests(\n" in text


def test_filters():
    assert frameworks.CATCH2.filter_arguments("a, [b]") == ["*a\\, \\[b]*"]
    assert frameworks.GTEST.filter_arguments("Suite.a-b") == ["--gtest_filter=*Suite.a?b*"]
