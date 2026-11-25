import pytest

from cxy.core import closure
from cxy.core.project import Project


@pytest.fixture
def project(tmp_path):
    files = {
        "cxy.toml": '[package]\nname = "app"\nversion = "1"\n',
        "include/app/net/http.hpp": "#include <string>\n",
        "include/app/json.hpp": "",
        "lib/net/http.cpp": '#include "app/net/http.hpp"\n#include <app/json.hpp>\n',
        "lib/old/http.cpp": "",
        "lib/json.cpp": '#include "json_impl.hpp"\n',
        "lib/json_impl.hpp": '#include "json_impl.hpp"\n',
        "lib/json_impl.cpp": "",
        "lib/unrelated.cpp": "",
        "lib/net/http_test.cpp": '#include "app/net/http.hpp"\n',
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return Project.find(tmp_path)


def test_follows_includes(project):
    assert closure.needed(project, project.sources(), "lib/net/http_test.cpp") == (
        "lib/json.cpp",
        "lib/json_impl.cpp",
        "lib/net/http.cpp",
    )


def test_names(project):
    assert {"http", "http_test", "http_test.cpp"} <= closure.test_names(
        project, "lib/net/http_test.cpp"
    )
