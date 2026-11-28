import pytest

from cxy.core import dependencies
from cxy.core.dependencies import Request, add_to, choose, parse_request, remove_from
from cxy.core.errors import CxyError
from cxy.core.project import Project

TEXT = """\
# top
[package]
name = "app"  # name
version = "1"

[dependencies]
fmt = "10.0.0"  # formatting
boost = { version = "1.80.0", options = { shared = true } }  # big

[dev-dependencies]
catch2 = "3.7.1"

[overrides]
zlib = "1.3"
"""


def replace(old, new):
    assert old in TEXT
    return TEXT.replace(old, new)


def test_add():
    assert add_to(TEXT, "dependencies", Request("spdlog", "1.14.1")) == replace(
        "# big\n", '# big\nspdlog = "1.14.1"\n'
    )
    bare = '[package]\nname = "app"\nversion = "1"\n'
    assert add_to(bare, "dependencies", Request("fmt", "1")) == (
        bare + '\n[dependencies]\nfmt = "1"\n'
    )


def test_update_keeps_the_rest():
    assert add_to(TEXT, "dependencies", Request("fmt", "11.0.2")) == replace("10.0.0", "11.0.2")
    assert add_to(TEXT, "dependencies", Request("boost", "1.86.0")) == replace("1.80.0", "1.86.0")


def test_options_are_merged():
    request = Request("boost", "1.80.0", {"header_only": True})
    assert "{shared = true, header_only = true}" in add_to(TEXT, "dependencies", request)
    request = Request("fmt", "11", {"shared": False}, force=True)
    expected = 'fmt = {version = "11", options = {shared = false}, force = true}'
    assert expected in add_to(TEXT, "dependencies", request)


def test_subtables():
    text = TEXT + '\n[dependencies.qt]\nversion = "6"  # six\n'
    text = text.replace("[dependencies]\n", "").replace('fmt = "10.0.0"  # formatting\n', "")
    text = text.replace('boost = { version = "1.80.0", options = { shared = true } }  # big\n', "")
    assert add_to(text, "dependencies", Request("qt", "7")) == text.replace('"6"', '"7"')
    assert "qt" not in remove_from(text, "qt")


def test_crlf_is_kept():
    text = TEXT.replace("\n", "\r\n")
    result = add_to(text, "dependencies", Request("fmt", "11"))
    assert result == text.replace("10.0.0", "11")


@pytest.mark.parametrize(
    ("section", "name", "message"),
    [
        ("dependencies", "catch2", "already in dev-dependencies"),
        ("dev-dependencies", "fmt", "already in dependencies"),
        ("dependencies", "zlib", "--override"),
        ("overrides", "fmt", "--force-version"),
        ("overrides", "catch2", "--force-version"),
    ],
)
def test_add_errors(section, name, message):
    with pytest.raises(CxyError, match=message):
        add_to(TEXT, section, Request(name, "1"))


def test_overrides():
    assert add_to(TEXT, "overrides", Request("zlib", "1.4")) == replace('"1.3"', '"1.4"')
    assert add_to(TEXT, "overrides", Request("bzip2", "1")) == TEXT + 'bzip2 = "1"\n'
    for request in Request("bzip2", "1", {"shared": True}), Request("bzip2", "1", force=True):
        with pytest.raises(CxyError, match="only a version"):
            add_to(TEXT, "overrides", request)


def test_remove():
    assert remove_from(TEXT, "fmt") == replace('fmt = "10.0.0"  # formatting\n', "")
    assert remove_from(TEXT, "catch2") == replace('catch2 = "3.7.1"\n', "")
    assert remove_from(TEXT, "zlib") == replace('zlib = "1.3"\n', "")
    with pytest.raises(CxyError, match="nope is not in"):
        remove_from(TEXT, "nope")


@pytest.mark.parametrize(
    ("text", "name", "version"),
    [
        ("fmt", "fmt", None),
        ("fmt@11.0.2", "fmt", "11.0.2"),
        ("fmt/11.0.2", "fmt", "11.0.2"),
        ("fmt/1.0@me/stable", "fmt", "1.0@me/stable"),
        ("fmt@[>=10 <11]", "fmt", "[>=10 <11]"),
    ],
)
def test_parse(text, name, version):
    assert parse_request(text) == Request(name, version)


def test_parse_options():
    assert parse_request("a", ["x=True", "y=false", "z=1=2"]).options == {
        "x": True,
        "y": False,
        "z": "1=2",
    }
    for bad in ("x", "=1"):
        with pytest.raises(CxyError):
            parse_request("a", [bad])
    for bad in ("", "fmt@", "/1", "a b"):
        with pytest.raises(CxyError):
            parse_request(bad)


@pytest.mark.parametrize(
    ("versions", "newest"),
    [
        (["9.1.0", "10.0.0", "10.0.0-rc1", "10.0.1-beta"], "10.0.0"),
        (["1.1.1", "1.1.1w", "1.1.1l"], "1.1.1w"),
        (["1.92.2b", "1.92.2", "1.92.9b-docking", "cci.20230105+1.89.2.docking"], "1.92.2b"),
        (["cci.20200203", "cci.20261021"], "cci.20261021"),
        (["1.2", "1.2.1"], "1.2.1"),
        ([], None),
    ],
)
def test_choose(versions, newest):
    assert choose(versions) == newest


def test_failure_to_lock_restores_the_manifest(tmp_path, monkeypatch):
    (tmp_path / "cxy.toml").write_text(TEXT)

    def fail(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(dependencies.pipeline, "lock", fail)
    with pytest.raises(KeyboardInterrupt):
        dependencies.remove(Project.find(tmp_path), ["fmt"], dependencies.BuildOptions())
    assert (tmp_path / "cxy.toml").read_text() == TEXT


def test_conditional_dependencies():
    path = ("when", "windows", "dependencies")
    added = add_to(TEXT, path, Request("wil", "1"))
    assert added == TEXT + '\n[when.windows.dependencies]\nwil = "1"\n'
    assert dependencies.find_section(__import__("tomllib").loads(added), "wil") == path
    again = add_to(added, ("when", "any(gcc, clang)", "dev-dependencies"), Request("rc", "2"))
    assert again.endswith('\n[when."any(gcc, clang)".dev-dependencies]\nrc = "2"\n')
    assert add_to(added, path, Request("wil", "2")) == added.replace('wil = "1"', 'wil = "2"')
    with pytest.raises(CxyError, match=r"wil is already in when\.windows\.dependencies"):
        add_to(added, "dependencies", Request("wil", "1"))
    with pytest.raises(CxyError, match="fmt is already in dependencies"):
        add_to(TEXT, path, Request("fmt", "1"))
    # Removing the last one removes its table.
    assert remove_from(added, "wil") == TEXT + "\n"
    assert remove_from(again, "rc") == added + "\n"
    assert dependencies.describe(("when", "any(gcc, clang)", "dependencies")) == (
        "when.'any(gcc, clang)'.dependencies"
    )
