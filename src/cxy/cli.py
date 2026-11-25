"""Command line entry point. Commands are imported only when needed, so their help is here."""

import sys
from importlib.metadata import version

from cyclopts import App

from cxy.core.errors import CxyError

app = App(
    name="cxy",
    help="A cargo-like project tool for C++, built on Conan, CMake and Ninja.",
    version=lambda: version("cxy"),
)
app.command("cxy.commands.create:new", name="new", help="Create a new project in a new directory.")
app.command(
    "cxy.commands.create:init", name="init", help="Create a project in the current directory."
)
app.command(
    "cxy.commands.build:build", name="build", help="Build the project, including its tests."
)
app.command("cxy.commands.build:run", name="run", help="Build and run the application.")
app.command("cxy.commands.build:test", name="test", help="Build and run the tests.")
app.command(
    "cxy.commands.export:export",
    name="export",
    help="Write Conan and CMake files for building without cxy.",
)
app.command("cxy.commands.fmt:fmt", name="fmt", help="Format C++, CMake and Conan files.")
app.command(
    "cxy.commands.lint:lint", name="lint", help="Find problems in C++, CMake and Conan files."
)
app.command("cxy.commands.clean:clean", name="clean", help="Delete the build directory.")


def main() -> None:
    try:
        result = app(result_action="return_value")
    except CxyError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(result if isinstance(result, int) else 0)
