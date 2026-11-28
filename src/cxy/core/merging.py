"""Three-way merges of cxy.toml, for ``cxy import --update``.

A setting is the value at a key path, such as ``lib.text.defines``. Arrays and inline tables are
single values, since their order and parts belong together. Each setting is compared in three
versions: what the last import proposed (the base), the current file (yours), and what importing
proposes now (upstream). Whatever changed on one side only is taken from it; a setting changed on
both, differently, is a conflict.
"""

import copy
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import tomlkit
from tomlkit.container import Container, OutOfOrderTableProxy
from tomlkit.items import Table

Path = tuple[str, ...]


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()
"""The value of a setting that a version does not have."""


@dataclass(frozen=True)
class Change:
    """Upstream's value for a setting that you have not changed."""

    path: Path
    before: Any
    after: Any


@dataclass(frozen=True)
class Conflict:
    """A setting that both you and upstream changed, differently."""

    path: Path
    base: Any
    """MISSING if it was not in the base, or if there was no base."""

    yours: Any
    upstream: Any

    def taking_upstream(self) -> Change:
        return Change(self.path, self.yours, self.upstream)


@dataclass(frozen=True)
class Plan:
    changes: Sequence[Change]
    conflicts: Sequence[Conflict]


def plan(base: str | None, yours: str, upstream: str) -> Plan:
    """Compare the settings of the three versions. Without a base, every difference conflicts."""
    base_values = settings(base) if base is not None else None
    your_values, upstream_values = settings(yours), settings(upstream)
    paths = list(your_values)
    paths += [p for p in upstream_values if p not in your_values]
    if base_values is not None:
        paths += [p for p in base_values if p not in your_values and p not in upstream_values]
    changes, conflicts = [], []
    for path in paths:
        mine = your_values.get(path, MISSING)
        theirs = upstream_values.get(path, MISSING)
        if mine == theirs:
            continue
        if base_values is None:
            conflicts.append(Conflict(path, MISSING, mine, theirs))
            continue
        original = base_values.get(path, MISSING)
        if original == theirs:
            continue  # Only you changed it.
        if original == mine:
            changes.append(Change(path, mine, theirs))
        else:
            conflicts.append(Conflict(path, original, mine, theirs))
    return Plan(changes, conflicts)


def apply(yours: str, upstream: str, changes: Sequence[Change]) -> str:
    """Return your text with upstream's values for these settings, keeping your formatting."""
    document = tomlkit.parse(yours)
    source = tomlkit.parse(upstream)
    created: list[Table] = []
    for change in changes:
        if change.after is MISSING:
            _remove(document, change.path, _table_paths(source))
        else:
            _set(document, change.path, _item(source, change.path), source, created)
    for table in created:
        if not table.is_super_table():
            table.add(tomlkit.nl())  # Before the next table's header.
    return tomlkit.dumps(document).rstrip("\n") + "\n"


def settings(text: str) -> dict[Path, Any]:
    """The plain value of each setting, by its key path."""
    return {path: _plain(item) for path, item in _leaves(tomlkit.parse(text), ())}


def name(path: Path) -> str:
    return ".".join(
        key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key) for key in path
    )


def show(value: Any) -> str:
    """A value as TOML would write it, on one line."""
    if value is MISSING:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, Mapping):
        return "{ " + ", ".join(f"{name((k,))} = {show(v)}" for k, v in value.items()) + " }"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(show(v) for v in value) + "]"
    return str(value)


def describe(change: Change) -> str:
    if change.after is MISSING:
        return f"{name(change.path)}: removed {show(change.before)}"
    return f"{name(change.path)}: {show(change.before)} -> {show(change.after)}"


def explain(conflict: Conflict) -> list[str]:
    """The lines that show a conflict."""
    return [
        name(conflict.path),
        f"  last import:  {show(conflict.base)}",
        f"  yours:        {_shown(conflict.yours, conflict.base)}",
        f"  upstream:     {_shown(conflict.upstream, conflict.base)}",
    ]


def _shown(value: Any, base: Any) -> str:
    return "(removed)" if value is MISSING and base is not MISSING else show(value)


def _is_table(item: Any) -> bool:
    return isinstance(item, (Table, OutOfOrderTableProxy, Container))


def _leaves(table: Any, prefix: Path) -> Iterator[tuple[Path, Any]]:
    for key, item in table.items():
        path = (*prefix, key)
        if _is_table(item):
            yield from _leaves(item, path)
        else:
            yield path, item


def _plain(item: Any) -> Any:
    return item.unwrap() if hasattr(item, "unwrap") else item


def _table_paths(document: Any) -> set[Path]:
    found: set[Path] = set()

    def visit(table: Any, prefix: Path) -> None:
        for key, item in table.items():
            if _is_table(item):
                found.add((*prefix, key))
                visit(item, (*prefix, key))

    visit(document, ())
    return found


def _item(document: Any, path: Path) -> Any:
    current = document
    for key in path:
        current = current[key]
    return copy.deepcopy(current)


def _set(document: Any, path: Path, item: Any, source: Any, created: list[Table]) -> None:
    current = document
    for depth, key in enumerate(path[:-1]):
        if key not in current or not _is_table(current[key]):
            # A table holding only tables, such as [lib] for [lib.core], is written as a header
            # of its own only if upstream's is.
            model = _item(source, path[: depth + 1])
            super_table = isinstance(model, Table) and model.is_super_table()
            table = tomlkit.table(is_super_table=super_table or None)
            current[key] = table
            created.append(table)
        current = current[key]
    current[path[-1]] = item


def _remove(document: Any, path: Path, kept: set[Path]) -> None:
    tables = [document]
    for key in path[:-1]:
        if key not in tables[-1]:
            return
        tables.append(tables[-1][key])
    del tables[-1][path[-1]]
    # Remove the tables left empty, unless upstream has them.
    for depth in range(len(path) - 1, 0, -1):
        if len(tables[depth]) == 0 and path[:depth] not in kept:
            del tables[depth - 1][path[depth - 1]]
        else:
            break
