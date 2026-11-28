"""Conditions on the Conan host settings, as in [when.'<condition>'] tables.

A condition is a shorthand such as ``windows`` or ``msvc``, a setting such as
``build_type = "Debug"``, or ``all(...)``, ``any(...)`` and ``not(...)`` of others.
"""

import difflib
import itertools
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from cxy.core.errors import CxyError

# Conan's values, with the CMAKE_SYSTEM_NAME and CMAKE_CXX_COMPILER_ID that they become.
SYSTEMS = {
    "Windows": "Windows",
    "WindowsStore": "WindowsStore",
    "WindowsCE": "WindowsCE",
    "Linux": "Linux",
    "Macos": "Darwin",
    "iOS": "iOS",
    "watchOS": "watchOS",
    "tvOS": "tvOS",
    "visionOS": "visionOS",
    "Android": "Android",
    "FreeBSD": "FreeBSD",
    "SunOS": "SunOS",
    "AIX": "AIX",
    "Emscripten": "Emscripten",
    "Neutrino": "QNX",
    "baremetal": "Generic",
    "VxWorks": "VxWorks",
}
COMPILERS = {
    "gcc": "GNU",
    "clang": "Clang",
    "apple-clang": "AppleClang",
    "msvc": "MSVC",
    "intel-cc": "IntelLLVM",
    "qcc": "QCC",
    "sun-cc": "SunPro",
    "mcst-lcc": "LCC",
}
BUILD_TYPES = ("Debug", "Release", "RelWithDebInfo", "MinSizeRel")
VALUES: Mapping[str, tuple[str, ...]] = {
    "os": tuple(SYSTEMS),
    "compiler": tuple(COMPILERS),
    "build_type": BUILD_TYPES,
}
_ONLY_ON = {
    "msvc": {"Windows", "WindowsStore", "WindowsCE"},
    "apple-clang": {"Macos", "iOS", "watchOS", "tvOS", "visionOS"},
}


@dataclass(frozen=True)
class Is:
    key: str
    value: str


@dataclass(frozen=True)
class All:
    parts: tuple["Condition", ...]


@dataclass(frozen=True)
class Any:
    parts: tuple["Condition", ...]


@dataclass(frozen=True)
class Not:
    part: "Condition"


Condition = Is | All | Any | Not

SHORTHANDS: Mapping[str, Condition] = {
    "windows": Is("os", "Windows"),
    "linux": Is("os", "Linux"),
    "macos": Is("os", "Macos"),
    "unix": Not(Is("os", "Windows")),
    "msvc": Is("compiler", "msvc"),
    "gcc": Is("compiler", "gcc"),
    "clang": Is("compiler", "clang"),
    "apple-clang": Is("compiler", "apple-clang"),
}
_COMBINERS = ("all", "any", "not")
_TOKEN = re.compile(r'\s*(?:(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)|(?P<string>"[^"]*")|(?P<op>[(),=]))')


def parse(text: str) -> Condition:
    """Read a condition, or raise CxyError saying what is wrong with it."""
    tokens = list(_tokens(text))
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take(expected: str | None = None) -> str:
        nonlocal position
        token = peek()
        if token is None:
            raise CxyError(f"condition {text!r} ends too soon")
        if expected is not None and token != expected:
            raise CxyError(f"condition {text!r}: expected {expected!r}, not {token!r}")
        position += 1
        return token

    def expression() -> Condition:
        name = take()
        if not _is_name(name):
            raise CxyError(f"condition {text!r}: unexpected {name!r}")
        if peek() == "(":
            if name not in _COMBINERS:
                raise CxyError(f"condition {text!r}: {name}(...) is not all, any or not")
            take("(")
            parts = [expression()]
            while peek() == ",":
                take(",")
                parts.append(expression())
            take(")")
            if name == "not":
                if len(parts) != 1:
                    raise CxyError(f"condition {text!r}: not(...) takes one condition")
                return Not(parts[0])
            return All(tuple(parts)) if name == "all" else Any(tuple(parts))
        if peek() == "=":
            take("=")
            value = take()
            if not value.startswith('"'):
                raise CxyError(f'condition {text!r}: write the value in quotes, as {name} = "..."')
            return _setting(text, name, value[1:-1])
        if name in SHORTHANDS:
            return SHORTHANDS[name]
        raise CxyError(f"condition {text!r}: unknown name {name!r}{_suggest(name, SHORTHANDS)}")

    condition = expression()
    if (extra := peek()) is not None:
        raise CxyError(f"condition {text!r}: unexpected {extra!r}")
    if not any(evaluate(condition, settings) for settings in combinations(condition)):
        raise CxyError(f"condition {text!r} can never be true")
    return condition


def evaluate(condition: Condition, settings: Mapping[str, str]) -> bool:
    match condition:
        case Is(key, value):
            return settings.get(key) == value
        case All(parts):
            return all(evaluate(part, settings) for part in parts)
        case Any(parts):
            return any(evaluate(part, settings) for part in parts)
        case Not(part):
            return not evaluate(part, settings)


def keys(condition: Condition) -> set[str]:
    """The settings that a condition depends on."""
    match condition:
        case Is(key, _):
            return {key}
        case All(parts) | Any(parts):
            return set().union(*map(keys, parts))
        case Not(part):
            return keys(part)


def combinations(condition: Condition) -> Iterator[dict[str, str]]:
    """Every possible value of the settings that a condition depends on."""
    names = sorted(keys(condition))
    for values in itertools.product(*(VALUES[name] for name in names)):
        settings = dict(zip(names, values, strict=True))
        compiler, system = settings.get("compiler"), settings.get("os")
        if compiler in _ONLY_ON and system is not None and system not in _ONLY_ON[compiler]:
            continue
        yield settings


def cmake_if(condition: Condition) -> str:
    """The condition as the arguments of a CMake if(), for settings other than build_type."""
    match condition:
        case Is("os", "Windows"):
            return "WIN32"
        case Is("os", value):
            return f'CMAKE_SYSTEM_NAME STREQUAL "{SYSTEMS[value]}"'
        case Is("compiler", value):
            return f'CMAKE_CXX_COMPILER_ID STREQUAL "{COMPILERS[value]}"'
        case Is(key, _):
            raise AssertionError(f"{key} has no if() form")
        case Any(parts) if all(isinstance(p, Is) and p.key == "compiler" for p in parts):
            ids = "|".join(COMPILERS[p.value] for p in parts if isinstance(p, Is))
            return f'CMAKE_CXX_COMPILER_ID MATCHES "^({ids})$"'
        case All(parts):
            return " AND ".join(_grouped(part) for part in parts)
        case Any(parts):
            return " OR ".join(_grouped(part) for part in parts)
        case Not(part):
            return f"NOT {_grouped(part)}"


def generator_expression(condition: Condition) -> str:
    """The condition as a CMake generator expression that is 1 or 0."""
    match condition:
        case Is("os", value):
            return f"$<PLATFORM_ID:{SYSTEMS[value]}>"
        case Is("compiler", value):
            return f"$<CXX_COMPILER_ID:{COMPILERS[value]}>"
        case Is("build_type", value):
            return f"$<CONFIG:{value}>"
        case Is(key, _):
            raise AssertionError(f"{key} has no generator expression")
        case All(parts):
            return f"$<AND:{','.join(map(generator_expression, parts))}>"
        case Any(parts):
            return f"$<OR:{','.join(map(generator_expression, parts))}>"
        case Not(part):
            return f"$<NOT:{generator_expression(part)}>"


def python(condition: Condition) -> str:
    """The condition as Python in a Conan recipe."""
    match condition:
        case Is(key, value):
            return f"self.settings.{key} == {value!r}"
        case All(parts):
            return " and ".join(_python_grouped(part) for part in parts)
        case Any(parts):
            return " or ".join(_python_grouped(part) for part in parts)
        case Not(part):
            return f"not {_python_grouped(part)}"


def _python_grouped(condition: Condition) -> str:
    text = python(condition)
    return f"({text})" if isinstance(condition, All | Any) and len(condition.parts) > 1 else text


def can_both_hold(first: Condition, second: Condition) -> bool:
    """Whether two conditions can be true for the same configuration."""
    both = All((first, second))
    return any(evaluate(both, settings) for settings in combinations(both))


def needs_generator_expression(condition: Condition) -> bool:
    """Whether CMake can only tell at build time, since multi-config generators choose then."""
    return "build_type" in keys(condition)


def _grouped(condition: Condition) -> str:
    text = cmake_if(condition)
    return f"({text})" if isinstance(condition, All | Any) and len(condition.parts) > 1 else text


def _setting(text: str, key: str, value: str) -> Condition:
    if key not in VALUES:
        raise CxyError(
            f"condition {text!r}: cxy cannot test {key!r}; use os, compiler or build_type"
            + _suggest(key, VALUES)
        )
    if value not in VALUES[key]:
        raise CxyError(
            f"condition {text!r}: {key} cannot be {value!r}; use one of {', '.join(VALUES[key])}"
        )
    return Is(key, value)


def _tokens(text: str) -> Iterator[str]:
    position = 0
    while position < len(text.rstrip()):
        match = _TOKEN.match(text, position)
        if match is None:
            raise CxyError(f"condition {text!r}: unexpected {text[position:].strip()[0]!r}")
        yield match.group(match.lastgroup or "op")
        position = match.end()


def _is_name(token: str) -> bool:
    return token[0].isalpha() or token[0] == "_"


def _suggest(name: str, known: Mapping[str, object]) -> str:
    close = difflib.get_close_matches(name, list(known), n=1)
    return f"; did you mean {close[0]!r}?" if close else ""
