from cxy.core import merging
from cxy.core.imported import Record
from cxy.core.merging import MISSING, Change, Conflict

BASE = """\
[package]
name = "app"
version = "1.0"

[dependencies]
fmt = "11.0.2"

[lib.core]
sources = "core"
defines = ["X"]

[bin.app]
main = "app/main.cpp"
"""


def test_each_side_keeps_what_only_it_changed():
    yours = BASE.replace('fmt = "11.0.2"', 'fmt = { version = "11.0.2", force = true }  # mine')
    yours += "\n[test]\nseparate = true\n"
    upstream = BASE.replace('version = "1.0"', 'version = "1.1"')
    upstream = upstream.replace("[dependencies]\n", '[dependencies]\nzlib = "1.3.1"\n')
    plan = merging.plan(BASE, yours, upstream)
    assert plan.conflicts == []
    assert plan.changes == [
        Change(("package", "version"), "1.0", "1.1"),
        Change(("dependencies", "zlib"), MISSING, "1.3.1"),
    ]
    merged = merging.apply(yours, upstream, plan.changes)
    assert 'fmt = { version = "11.0.2", force = true }  # mine\nzlib = "1.3.1"\n' in merged
    assert 'version = "1.1"' in merged and merged.endswith("[test]\nseparate = true\n")


def test_settings_changed_on_both_sides():
    yours = BASE.replace('defines = ["X"]', 'defines = ["X", "MINE"]')
    upstream = BASE.replace('defines = ["X"]', 'defines = ["Y"]')
    conflict = Conflict(("lib", "core", "defines"), ["X"], ["X", "MINE"], ["Y"])
    assert merging.plan(BASE, yours, upstream).conflicts == [conflict]
    assert merging.explain(conflict) == [
        "lib.core.defines",
        '  last import:  ["X"]',
        '  yours:        ["X", "MINE"]',
        '  upstream:     ["Y"]',
    ]
    merged = merging.apply(yours, upstream, [conflict.taking_upstream()])
    assert 'defines = ["Y"]' in merged
    # The same change on both sides is no conflict.
    assert merging.plan(BASE, upstream, upstream) == merging.Plan([], [])


def test_removals():
    upstream = BASE.replace('\n[bin.app]\nmain = "app/main.cpp"\n', "")
    plan = merging.plan(BASE, BASE, upstream)
    assert [merging.describe(c) for c in plan.changes] == ['bin.app.main: removed "app/main.cpp"']
    assert merging.apply(BASE, upstream, plan.changes) == upstream
    # Removing what you changed is a conflict.
    yours = BASE.replace('main = "app/main.cpp"', 'main = "app/other.cpp"')
    (conflict,) = merging.plan(BASE, yours, upstream).conflicts
    assert merging.explain(conflict)[3] == "  upstream:     (removed)"


def test_new_tables():
    upstream = BASE.replace("[bin.app]", '[lib.hdr]\nsources = "headers"\n\n[bin.app]')
    upstream += '\n[build]\ndefines = ["A"]\n'
    plan = merging.plan(BASE, BASE, upstream)
    assert (
        merging.apply(BASE, upstream, plan.changes)
        == BASE.replace("[bin.app]", '[lib.hdr]\nsources = "headers"\n\n[bin.app]')
        + '\n[build]\ndefines = ["A"]\n'
    )
    plain = '[package]\nname = "app"\n'
    named = plain + '\n[lib.core]\nsources = "core"\n'
    assert merging.apply(plain, named, merging.plan(plain, plain, named).changes) == named


def test_without_a_base_every_difference_conflicts():
    upstream = BASE.replace('version = "1.0"', 'version = "1.1"')
    (conflict,) = merging.plan(None, BASE, upstream).conflicts
    assert conflict == Conflict(("package", "version"), MISSING, "1.0", "1.1")
    assert merging.explain(conflict)[1] == "  last import:  (none)"


def test_show():
    assert merging.show({"version": "1", "options": {"shared": True}, "n": 2}) == (
        '{ version = "1", options = { shared = true }, n = 2 }'
    )
    assert merging.name(("dependencies", "a.b")) == 'dependencies."a.b"'


def test_record(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(a)\n")
    (tmp_path / "conanfile.txt").write_text("")
    record = Record.of(tmp_path, BASE, ["conanfile.txt", "CMakeLists.txt", "CMakeLists.txt"])
    assert list(record.inputs) == ["CMakeLists.txt", "conanfile.txt"]
    assert record.save(tmp_path)
    assert Record.load(tmp_path) == record and record.changed(tmp_path) == []
    (tmp_path / "CMakeLists.txt").write_text("project(b)\n")
    (tmp_path / "conanfile.txt").unlink()
    assert record.changed(tmp_path) == ["CMakeLists.txt", "conanfile.txt"]
    assert Record.load(tmp_path / "missing") is None


def test_asking_about_a_conflict(monkeypatch, capsys):
    from cxy.commands import importing

    answers = iter(["maybe", "U"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    conflict = Conflict(("package", "version"), "1.0", "1.1", "2.0")
    assert importing._ask(conflict) is True
    assert capsys.readouterr().out.startswith("Conflict in package.version\n  last import:")
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert importing._ask(conflict) is False
