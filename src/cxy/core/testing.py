"""Plan the test programs of a project, and choose a framework for each."""

import re
from collections.abc import Callable, Set
from dataclasses import dataclass

from cxy.core import closure, frameworks
from cxy.core.errors import CxyError
from cxy.core.frameworks import Framework
from cxy.core.manifest import Manifest
from cxy.core.project import Project, Sources

_TEST_MACRO = re.compile(
    r"^[ \t]*(?:(?:TYPED_)?TEST(?:_[FP])?|(?:CATCH_)?SCENARIO(?:_METHOD)?"
    r"|[A-Z_]*TEST_CASE[A-Z_]*)[ \t]*\(",
    re.MULTILINE,
)


@dataclass(frozen=True)
class TestProgram:
    __test__ = False  # Not a pytest class.

    target: str
    sources: tuple[str, ...]
    """The sources to compile. If ``is_unit``, the first is the test and the rest are helpers."""

    is_unit: bool
    """Whether this is for a single test file."""

    framework: Framework
    in_tests: bool = False
    """Whether the test is in the tests directory."""

    @property
    def isolated_target(self) -> str:
        return f"{self.target}_isolated"


def plan(project: Project, sources: Sources) -> list[TestProgram]:
    return plan_programs(
        project.manifest,
        sources,
        lambda source: detect(project, source),
        lambda source: is_test(project, source),
    )


def is_test(project: Project, source: str) -> bool:
    """Whether a source in the tests directory has tests, rather than being a helper."""
    if closure.tested_name(project, source) is not None:
        return True
    text = (project.root / source).read_text(encoding="utf-8", errors="replace")
    return _TEST_MACRO.search(text) is not None


def detect(project: Project, source: str) -> set[str]:
    """Return the frameworks that a source includes, directly or through project headers."""
    found: set[str] = set()
    seen: set[str] = set()
    pending = [source]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for include in closure.include_names(project, current):
            found |= {
                name
                for name, framework in frameworks.ALL.items()
                if include.startswith(framework.include_prefixes)
            }
        pending += closure.included_files(project, current)
    return found


def _unavailable(manifest: Manifest, source: str, framework: str, available: list[str]) -> CxyError:
    message = (
        f"{source} uses {framework}, but [dev-dependencies] only has {' and '.join(available)}"
    )
    for dependency in manifest.dependencies:
        if dependency.name == framework:
            return CxyError(f"{message}; move {framework} there from [dependencies]")
    return CxyError(f"{message}; add it with 'cxy add --dev {framework}'")


def plan_programs(
    manifest: Manifest,
    sources: Sources,
    classify: Callable[[str], Set[str]],
    is_test: Callable[[str], bool] = lambda _: True,
) -> list[TestProgram]:
    """Plan the programs.

    ``classify`` finds which frameworks a source includes, and ``is_test`` whether a source in
    the tests directory has tests.

    Each program has one framework. Each framework has a main(), and with two of them the
    linker may silently choose one, so that the other's tests never run.
    """
    if not sources.unit_tests and not sources.tests:
        return []
    available = [d.name for d in manifest.dev_dependencies if d.name in frameworks.ALL]
    if not available:
        raise CxyError(
            f"tests need a test framework: add {' or '.join(frameworks.ALL)} to "
            "[dev-dependencies], for example with 'cxy add --dev catch2'"
        )
    preferred = manifest.test_framework
    if preferred is not None and preferred not in available:
        raise CxyError(f"test.framework is {preferred}, which is not in [dev-dependencies]")

    def choose(source: str) -> str | None:
        included = set(classify(source))
        found = sorted(included & set(available))
        if included and not found:
            # With another framework's main(), these tests would be built but never run.
            raise _unavailable(manifest, source, min(included), available)
        if len(available) == 1:
            return available[0]
        if len(found) > 1:
            raise CxyError(
                f"{source} includes both {' and '.join(found)}, but one program can only run "
                "the tests of one; split it"
            )
        return found[0] if found else None

    def undecided(what: str) -> CxyError:
        return CxyError(
            f"cannot tell whether {what} uses {' or '.join(available)}: include one of them, or "
            "set framework in the [test] section of cxy.toml"
        )

    name = manifest.name
    chosen = {source: choose(source) for source in sources.tests}
    separate: list[str] = []
    if manifest.test_separate and sources.tests:
        separate = [source for source in sources.tests if is_test(source)]
        if not separate:
            raise CxyError(f"no tests found in {manifest.layout.tests}/")

    groups: dict[str, list[str]] = {}
    shared: list[str] = []
    for source, framework in chosen.items():
        if framework is None:
            shared.append(source)
        else:
            groups.setdefault(framework, []).append(source)
    if separate:
        groups = {}
    elif shared and not groups:
        if preferred is None:
            raise undecided(f"{manifest.layout.tests}/")
        groups[preferred] = []

    used = {f"{name}_tests"}
    for found in sources.components:
        used |= {found.component.target, found.component.objects}
    used |= {f"{name}_tests_{framework}" for framework in groups}
    programs = []
    for source in (*sources.unit_tests, *separate):
        in_tests = source in chosen
        framework = (chosen[source] if in_tests else choose(source)) or preferred
        if framework is None:
            raise undecided(source)
        helpers: list[str] = []
        if in_tests:
            helpers = [
                helper
                for helper in sources.tests
                if helper not in separate and chosen[helper] in (None, framework)
            ]
        stem = re.sub(r"[^A-Za-z0-9_]", "_", source.rpartition(".")[0])
        base = target = f"{name}_{stem}"
        number = 2
        while target in used:
            target = f"{base}_{number}"
            number += 1
        used |= {target, f"{target}_isolated"}
        programs.append(
            TestProgram(
                target, (source, *helpers), True, frameworks.ALL[framework], in_tests=in_tests
            )
        )
    for framework in sorted(groups):
        # Files that use neither framework are helpers, which any of the programs may need.
        files = tuple(sorted([*groups[framework], *shared]))
        target = f"{name}_tests" if len(groups) == 1 else f"{name}_tests_{framework}"
        programs.append(TestProgram(target, files, False, frameworks.ALL[framework], in_tests=True))
    return programs
