"""What differs between test frameworks."""

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cxy.core.conan import CMakeInfo
from cxy.core.manifest import TEST_FRAMEWORKS, Dependency


@dataclass(frozen=True)
class Framework:
    package: str
    """The name of its Conan package."""

    include_prefixes: tuple[str, ...]
    cmake_module: str
    discover_command: str
    main_targets: tuple[str, ...]
    """The targets to link, best first. The last is for packages built without main()."""

    default_target: str

    def links(self, dependency: Dependency, graph: Mapping[str, CMakeInfo]) -> tuple[str, ...]:
        if dependency.cmake_targets:
            return dependency.cmake_targets
        info = graph.get(dependency.name)
        available = info.components if info else frozenset()
        for target in self.main_targets:
            if target in available:
                return (target,)
        return (self.default_target,)

    def filter_arguments(self, text: str) -> list[str]:
        """Return the arguments to run tests with this text in their names."""
        raise NotImplementedError

    def ran_nothing(self, executable: Path, arguments: Sequence[str], code: int) -> bool:
        """Whether a program that exited with this code had no matching tests."""
        raise NotImplementedError


class _Catch2(Framework):
    def filter_arguments(self, text: str) -> list[str]:
        return ["*" + re.sub(r'([\\,\["~])', r"\\\1", text) + "*"]

    def ran_nothing(self, executable: Path, arguments: Sequence[str], code: int) -> bool:
        # Some versions also exit with this code when that many assertions fail.
        if code != 2:
            return False
        result = subprocess.run(
            [str(executable), *arguments, "--allow-running-no-tests"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        return result.returncode == 0


class _GoogleTest(Framework):
    def filter_arguments(self, text: str) -> list[str]:
        # These separate patterns and cannot be escaped, so match any character instead.
        return ["--gtest_filter=*" + re.sub(r"[:-]", "?", text) + "*"]

    def ran_nothing(self, executable: Path, arguments: Sequence[str], code: int) -> bool:
        # Most versions succeed when no tests match, so ask which do. Listing tests writes the
        # list to any output file, which would replace the results.
        arguments = [a for a in arguments if not re.match("--?gtest_output", a)]
        environment = {k: v for k, v in os.environ.items() if k != "GTEST_OUTPUT"}
        result = subprocess.run(
            [str(executable), *arguments, "--gtest_list_tests"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=environment,
            text=True,
            errors="replace",
        )
        lines = result.stdout.splitlines()
        return result.returncode == 0 and all(
            not line.strip() or line.startswith(("Running main() from", "Note: ")) for line in lines
        )


CATCH2 = _Catch2(
    package="catch2",
    include_prefixes=("catch2/",),
    cmake_module="Catch",
    discover_command="catch_discover_tests",
    main_targets=("Catch2::Catch2WithMain",),
    default_target="Catch2::Catch2WithMain",
)
GTEST = _GoogleTest(
    package="gtest",
    include_prefixes=("gtest/", "gmock/"),
    cmake_module="GoogleTest",
    discover_command="gtest_discover_tests",
    # gmock's main() initializes gtest too.
    main_targets=("GTest::gmock_main", "GTest::gtest_main", "GTest::gmock", "GTest::gtest"),
    default_target="GTest::gtest_main",
)
ALL = {framework.package: framework for framework in (CATCH2, GTEST)}
assert set(ALL) == set(TEST_FRAMEWORKS)
