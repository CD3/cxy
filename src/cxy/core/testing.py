"""Plan the test programs of a project, and choose a framework for each."""

import re
from collections.abc import Callable, Set
from dataclasses import dataclass

from cxy.core import closure, frameworks
from cxy.core.errors import CxyError
from cxy.core.frameworks import Framework
from cxy.core.manifest import Manifest
from cxy.core.project import Project, Sources


@dataclass(frozen=True)
class TestProgram:
    __test__ = False  # Not a pytest class.

    target: str
    sources: tuple[str, ...]
    is_unit: bool
    framework: Framework

    @property
    def isolated_target(self) -> str:
        return f"{self.target}_isolated"


def plan(project: Project, sources: Sources) -> list[TestProgram]:
    return plan_programs(project.manifest, sources, lambda source: detect(project, source))


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


def plan_programs(
    manifest: Manifest, sources: Sources, classify: Callable[[str], Set[str]]
) -> list[TestProgram]:
    """Plan the programs, using ``classify`` to find which frameworks a source includes.

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
        if len(available) == 1:
            return available[0]
        found = sorted(set(classify(source)) & set(available))
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
    groups: dict[str, list[str]] = {}
    shared: list[str] = []
    for source in sources.tests:
        framework = choose(source)
        if framework is None:
            shared.append(source)
        else:
            groups.setdefault(framework, []).append(source)
    if shared and not groups:
        if preferred is None:
            raise undecided(f"{manifest.layout.tests}/")
        groups[preferred] = []

    used = {name, f"{name}_lib", f"{name}_app", f"{name}_tests"}
    used |= {f"{name}_tests_{framework}" for framework in groups}
    programs = []
    for source in sources.unit_tests:
        framework = choose(source) or preferred
        if framework is None:
            raise undecided(source)
        stem = re.sub(r"[^A-Za-z0-9_]", "_", source.rpartition(".")[0])
        base = target = f"{name}_{stem}"
        number = 2
        while target in used:
            target = f"{base}_{number}"
            number += 1
        used |= {target, f"{target}_isolated"}
        programs.append(TestProgram(target, (source,), True, frameworks.ALL[framework]))
    for framework in sorted(groups):
        # Files that use neither framework are helpers, which any of the programs may need.
        files = tuple(sorted([*groups[framework], *shared]))
        target = f"{name}_tests" if len(groups) == 1 else f"{name}_tests_{framework}"
        programs.append(TestProgram(target, files, False, frameworks.ALL[framework]))
    return programs
