"""验证从任意工作目录启动时路径保持稳定。"""

from pathlib import Path

from src.config.settings import PROJECT_ROOT, Settings


def test_settings_ignore_working_directory(tmp_path: Path, monkeypatch) -> None:
    """切换工作目录后仍加载项目配置和已下载缓存。"""
    expected = Settings.load()
    monkeypatch.chdir(tmp_path)
    settings = Settings.load()
    assert settings.data.root == expected.data.root
    assert Path(settings.data.credentials_file).parent == PROJECT_ROOT
    assert Path(settings.report.root) == PROJECT_ROOT / "output"


def test_absolute_paths_are_preserved(tmp_path: Path) -> None:
    """用户显式配置的绝对路径不应被改写。"""
    import json

    content = Settings().to_dict()
    content["data"]["root"] = str(tmp_path / "cache")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    assert Settings.load(path).data.root == str(tmp_path / "cache")
