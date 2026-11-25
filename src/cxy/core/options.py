"""Reject options that a package does not have, which Conan silently ignores."""

import difflib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cxy.core import tools
from cxy.core.errors import CxyError
from cxy.core.manifest import Manifest


def check(manifest: Manifest, graph: str, cwd: Path) -> None:
    """Check the manifest's options against ``conan graph info --format=json`` output."""
    nodes = _direct_nodes(graph)
    problems = []
    for dependency in (*manifest.dependencies, *manifest.dev_dependencies):
        node = nodes.get(dependency.name)
        if node is None:
            continue
        valid = declared(node)
        if accepts(valid, dependency.options):
            continue
        # A recipe can remove options that do not apply to this configuration, so that they
        # are missing from the graph. Ask for all of them before rejecting any.
        valid |= _all_options(node, cwd)
        if not accepts(valid, dependency.options):
            problems += [
                describe(dependency.name, option, valid)
                for option in dependency.options
                if option not in valid
            ]
    if problems:
        raise CxyError("; ".join(problems))


def declared(recipe: Mapping[str, Any]) -> set[str]:
    """Return the names of a recipe's own options."""
    names: set[str] = set()
    for key in ("options_definitions", "options", "default_options"):
        values = recipe.get(key)
        if isinstance(values, dict):
            # Default options may include those of other packages, such as "zlib/*:shared".
            names |= {name for name in values if ":" not in name}
    return names


def accepts(valid: set[str], options: Mapping[str, Any]) -> bool:
    return "*" in valid or options.keys() <= valid


def describe(package: str, option: str, valid: set[str]) -> str:
    message = f"{package} has no option {option!r}"
    if close := difflib.get_close_matches(option, sorted(valid), n=1):
        message += f"; did you mean {close[0]!r}?"
    if valid:
        return f"{message} (its options are {', '.join(sorted(valid))})"
    return f"{message} (it has none)"


def _direct_nodes(graph: str) -> dict[str, Mapping[str, Any]]:
    try:
        nodes = json.loads(graph)["graph"]["nodes"].values()
        return {node["name"]: node for node in nodes if node.get("context") == "host"}
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise CxyError("could not understand Conan's JSON output") from error


def _all_options(node: Mapping[str, Any], cwd: Path) -> set[str]:
    folder = node.get("recipe_folder")
    if not folder:
        return {"*"}  # Unknown, so do not reject anything.
    recipe = str(Path(folder) / "conanfile.py")
    output = tools.run("conan", "inspect", recipe, "--format=json", cwd=cwd, capture=True)
    try:
        return declared(json.loads(output))
    except (ValueError, AttributeError) as error:
        raise CxyError("could not understand Conan's JSON output") from error
