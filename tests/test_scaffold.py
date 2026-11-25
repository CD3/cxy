from cxy.core import formatting, scaffold
from cxy.core.workspace import Workspace


def test_new_project_is_formatted(tmp_path):
    scaffold.new(tmp_path / "demo")
    assert formatting.fmt(Workspace.find(tmp_path / "demo"), check=True) == 0


def test_init_does_not_impose_a_style_on_existing_code(tmp_path):
    (tmp_path / "src").mkdir()
    scaffold.init(tmp_path, name="demo")
    assert not (tmp_path / ".clang-format").exists()
