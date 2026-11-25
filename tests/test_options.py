import json

import pytest

from cxy.core import options
from cxy.core.errors import CxyError
from cxy.core.manifest import parse


def manifest(text):
    return parse(f'[package]\nname = "app"\nversion = "1"\n[dependencies]\n{text}\n')


def graph(**node):
    node = {"name": "fmt", "context": "host", "recipe_folder": "/recipe", **node}
    return json.dumps({"graph": {"nodes": {"1": node}}})


@pytest.fixture
def inspect(monkeypatch):
    """Set what "conan inspect" reports, and count its uses."""
    calls = []

    def _inspect(**recipe):
        def run(*arguments, **_):
            calls.append(arguments)
            return json.dumps(recipe)

        monkeypatch.setattr(options.tools, "run", run)
        return calls

    return _inspect


FMT = {"options_definitions": {"shared": ["True", "False"], "fPIC": ["True", "False"]}}


def test_known_option_needs_no_inspection(inspect, tmp_path):
    calls = inspect()
    text = 'fmt = { version = "1", options = { shared = true } }'
    options.check(manifest(text), graph(**FMT), tmp_path)
    assert not calls


def test_unknown_option(inspect, tmp_path):
    inspect(**FMT)
    text = 'fmt = { version = "1", options = { shard = true } }'
    with pytest.raises(CxyError, match=r"did you mean 'shared'\? \(its options are fPIC, shared"):
        options.check(manifest(text), graph(**FMT), tmp_path)


def test_option_removed_for_this_configuration(inspect, tmp_path):
    calls = inspect(**FMT)
    text = 'fmt = { version = "1", options = { shared = true } }'
    options.check(manifest(text), graph(options_definitions={}), tmp_path)
    assert len(calls) == 1


def test_options_of_other_packages_do_not_count(inspect, tmp_path):
    inspect(default_options={"zlib/*:shared": True})
    text = 'fmt = { version = "1", options = { shared = true } }'
    with pytest.raises(CxyError, match="it has none"):
        options.check(manifest(text), graph(default_options={"zlib/*:shared": True}), tmp_path)


def test_every_problem_is_reported(inspect, tmp_path):
    inspect(**FMT)
    text = 'fmt = { version = "1", options = { aaa = 1, bbb = 2 } }'
    with pytest.raises(CxyError, match=r"'aaa'.*'bbb'"):
        options.check(manifest(text), graph(**FMT), tmp_path)


def test_unavailable_recipe_rejects_nothing(tmp_path):
    text = 'fmt = { version = "1", options = { shard = true } }'
    options.check(manifest(text), graph(recipe_folder=None), tmp_path)
