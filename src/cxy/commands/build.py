from dataclasses import asdict, dataclass
from typing import Annotated

from cyclopts import Parameter

from cxy.core import pipeline
from cxy.core.project import Project


@Parameter(name="*")
@dataclass(frozen=True)
class ResolveOptions:
    release: Annotated[bool, Parameter(name=["--release", "-r"], negative="")] = False
    """Build with optimizations."""

    profile: str = "default"
    """Conan profile for the host and build contexts."""

    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"], negative="")] = False
    """Show full tool output."""

    def to_core(self) -> pipeline.BuildOptions:
        return pipeline.BuildOptions(**asdict(self))


@Parameter(name="*")
@dataclass(frozen=True)
class CommonOptions(ResolveOptions):
    refresh: Annotated[bool, Parameter(negative="")] = False
    """Rerun Conan and CMake even if nothing has changed."""

    locked: Annotated[bool, Parameter(negative="")] = False
    """Fail if cxy.lock is missing or out of date, instead of updating it."""


@Parameter(name="*")
@dataclass(frozen=True)
class Options(CommonOptions):
    jobs: Annotated[int | None, Parameter(name=["--jobs", "-j"])] = None
    """Number of parallel build jobs."""


def build(*, options: Options | None = None) -> None:
    """Build the project, including its tests."""
    pipeline.build(Project.find(), (options or Options()).to_core())


def run(*args: str, options: Options | None = None) -> int:
    """Build and run the application.

    Parameters
    ----------
    args
        Arguments for the application. Put them after ``--``.
    """
    return pipeline.run(Project.find(), (options or Options()).to_core(), args)


def test(
    *args: str,
    tests: Annotated[tuple[str, ...], Parameter(name=["--test", "-t"], negative="")] = (),
    name_filter: Annotated[str | None, Parameter(name=["--filter", "-f"])] = None,
    isolated: Annotated[bool, Parameter(negative="")] = False,
    ctest: Annotated[bool, Parameter(negative="")] = False,
    options: Options | None = None,
) -> int:
    """Build and run the tests.

    Parameters
    ----------
    args
        Arguments for the test programs, such as ``--help``. Put them after ``--``.
    tests
        Only build and run this test: a file, its name, or the name of the source it tests.
        May be repeated.
    name_filter
        Only run tests with this text in their names, whichever framework they use.
    isolated
        Compile only the sources that the chosen tests need, found by following their
        includes, so that code elsewhere may be broken.
    ctest
        Run each test case in its own process with CTest. Arguments after ``--`` go to CTest, and
        ``--jobs`` also sets how many tests run at once.
    """
    core = (options or Options()).to_core()
    return pipeline.test(
        Project.find(),
        core,
        args,
        names=tests,
        isolated=isolated,
        name_filter=name_filter,
        ctest=ctest,
    )
