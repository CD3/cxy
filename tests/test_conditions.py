import pytest

from cxy.core import conditions
from cxy.core.conditions import All, Any, Is, Not
from cxy.core.errors import CxyError

LINUX_GCC = {"os": "Linux", "compiler": "gcc", "build_type": "Debug", "arch": "x86_64"}
WINDOWS_MSVC = {"os": "Windows", "compiler": "msvc", "build_type": "Release"}


def test_parse():
    assert conditions.parse("windows") == Is("os", "Windows")
    assert conditions.parse("unix") == Not(Is("os", "Windows"))
    assert conditions.parse('build_type = "Debug"') == Is("build_type", "Debug")
    assert conditions.parse(" all( windows , not(msvc) ) ") == All(
        (Is("os", "Windows"), Not(Is("compiler", "msvc")))
    )
    assert conditions.parse('any(gcc, compiler="clang")') == Any(
        (Is("compiler", "gcc"), Is("compiler", "clang"))
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("windos", "unknown name 'windos'; did you mean 'windows'"),
        ('os = "windows"', "os cannot be 'windows'; use one of Windows"),
        ('arch = "x86_64"', "cannot test 'arch'"),
        ("os = Windows", "write the value in quotes"),
        ("not(windows, msvc)", "not\\(...\\) takes one condition"),
        ("first(windows)", "is not all, any or not"),
        ("all(windows", "ends too soon"),
        ("windows linux", "unexpected 'linux'"),
        ("windows!", "unexpected '!'"),
        ("all(windows, linux)", "can never be true"),
        ("all(linux, msvc)", "can never be true"),
        ("all(windows, apple-clang)", "can never be true"),
    ],
)
def test_parse_errors(text, message):
    with pytest.raises(CxyError, match=message):
        conditions.parse(text)


def test_evaluate():
    clang_or_gcc = conditions.parse("any(gcc, clang)")
    assert conditions.evaluate(clang_or_gcc, LINUX_GCC)
    assert not conditions.evaluate(clang_or_gcc, WINDOWS_MSVC)
    # Apple's Clang is not Clang, as for Conan and CMake.
    assert not conditions.evaluate(clang_or_gcc, {"os": "Macos", "compiler": "apple-clang"})
    assert conditions.evaluate(conditions.parse("unix"), LINUX_GCC)
    assert conditions.evaluate(conditions.parse('all(windows, build_type="Release")'), WINDOWS_MSVC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("windows", "WIN32"),
        ("unix", "NOT WIN32"),
        ("macos", 'CMAKE_SYSTEM_NAME STREQUAL "Darwin"'),
        ("msvc", 'CMAKE_CXX_COMPILER_ID STREQUAL "MSVC"'),
        (
            "any(gcc, clang, apple-clang)",
            'CMAKE_CXX_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"',
        ),
        ("all(windows, msvc)", 'WIN32 AND CMAKE_CXX_COMPILER_ID STREQUAL "MSVC"'),
        (
            "any(linux, all(windows, not(msvc)))",
            'CMAKE_SYSTEM_NAME STREQUAL "Linux" OR '
            '(WIN32 AND NOT CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")',
        ),
        ("not(any(windows, macos))", 'NOT (WIN32 OR CMAKE_SYSTEM_NAME STREQUAL "Darwin")'),
    ],
)
def test_cmake_if(text, expected):
    assert conditions.cmake_if(conditions.parse(text)) == expected


def test_generator_expression():
    condition = conditions.parse('all(windows, not(build_type = "Debug"), any(msvc, clang))')
    assert conditions.needs_generator_expression(condition)
    assert not conditions.needs_generator_expression(conditions.parse("msvc"))
    assert conditions.generator_expression(condition) == (
        "$<AND:$<PLATFORM_ID:Windows>,$<NOT:$<CONFIG:Debug>>,"
        "$<OR:$<CXX_COMPILER_ID:MSVC>,$<CXX_COMPILER_ID:Clang>>>"
    )


def test_python():
    condition = conditions.parse('all(windows, not(any(msvc, compiler = "clang")))')
    assert conditions.python(condition) == (
        "self.settings.os == 'Windows' and not "
        "(self.settings.compiler == 'msvc' or self.settings.compiler == 'clang')"
    )


def test_can_both_hold():
    windows, unix, msvc = map(conditions.parse, ("windows", "unix", "msvc"))
    assert not conditions.can_both_hold(windows, unix)
    assert conditions.can_both_hold(windows, msvc)
    assert not conditions.can_both_hold(msvc, conditions.parse("linux"))
