import json

import pytest

from cxy.core import lock as locks
from cxy.core.errors import CxyError
from cxy.core.lock import Lock
from cxy.core.manifest import parse

PACKAGE = '[package]\nname = "app"\nversion = "1"\n'
CONAN = {
    "version": "0.5",
    "requires": ["fmt/10.2.0#abc%1", "spdlog/1.14.1#def%2"],
    "build_requires": ["cmake/3.30#x%1"],
    "overrides": {"fmt/10.2.1": ["fmt/10.2.0"]},
}


def manifest(text=""):
    return parse(PACKAGE + text)


def test_round_trip(tmp_path):
    path = tmp_path / locks.FILENAME
    assert Lock.load(path) is None
    lock = Lock(locks.inputs(manifest()), CONAN)
    assert lock.save(path)
    assert not lock.save(path)
    assert Lock.load(path) == lock
    assert json.loads(lock.conan_text()) == CONAN


@pytest.mark.parametrize("text", ["", "<<<<<<< HEAD", "[]", '{"version": 1}'])
def test_unreadable(tmp_path, text):
    path = tmp_path / locks.FILENAME
    path.write_text(text)
    with pytest.raises(CxyError, match="cannot read"):
        Lock.load(path)


@pytest.mark.parametrize(
    "change",
    [
        '[dependencies]\nfmt = "2"\n',
        '[dependencies]\nfmt = { version = "1", force = true }\n',
        '[dependencies]\nfmt = { version = "1", options = { shared = true } }\n',
        '[dev-dependencies]\nfmt = "1"\n',
        '[dependencies]\nfmt = "1"\n[overrides]\nzlib = "1.3"\n',
        "",
    ],
)
def test_notices_dependency_changes(change):
    lock = Lock(locks.inputs(manifest('[dependencies]\nfmt = "1"\n')), CONAN)
    assert not lock.matches(manifest(change))


def test_ignores_unrelated_changes():
    lock = Lock(locks.inputs(manifest('[dependencies]\nfmt = "1"\nzlib = "1"\n')), CONAN)
    reordered = '[dependencies]\nzlib = "1"\nfmt = "1"\n[layout]\nlib = "source"\n'
    assert lock.matches(manifest(reordered))


def test_removing():
    lock = Lock({}, CONAN)
    assert "overrides" not in lock.without_overrides().conan
    assert set(lock.without_package("fmt").packages()) == {"spdlog", "cmake"}
    with pytest.raises(CxyError, match="not in"):
        lock.without_package("fm")


def test_changes():
    after = Lock({}, {"requires": ["fmt/11.0#a", "zlib/1.3#b"]})
    assert locks.changes(Lock({}, CONAN), after) == [
        "removed cmake 3.30",
        "changed fmt 10.2.0 -> 11.0",
        "removed spdlog 1.14.1",
        "added zlib 1.3",
    ]


def test_conditional_inputs(tmp_path):
    plain = manifest('[dependencies]\nfmt = "11"\n')
    assert "when" not in locks.inputs(plain)
    conditional = manifest('[dependencies]\nfmt = "11"\n[when.windows.dependencies]\nwil = "1"\n')
    inputs = locks.inputs(conditional)
    assert inputs["when"] == {
        "windows": {"dependencies": {"wil": {"version": "1"}}, "dev-dependencies": {}}
    }
    # The same on every platform, so that a lock for one keeps what others need.
    assert locks.inputs(conditional.resolve({"os": "Linux"})) == inputs
    assert locks.conditional_names(conditional.resolve({"os": "Linux"})) == {"wil"}

    path = tmp_path / locks.FILENAME
    lock = Lock(inputs, CONAN, {"wil": {"package": "wil", "target": "WIL::WIL"}})
    lock.save(path)
    assert Lock.load(path) == lock
    assert lock.without_package("fmt").cmake == lock.cmake
    Lock(inputs, CONAN).save(path)
    assert "cmake" not in json.loads(path.read_text())
