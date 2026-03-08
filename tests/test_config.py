"""Tests for Lumos config."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from lumos.core.config import LumosConfig, load_config, save_config, _deep_update


def test_default_config():
    config = LumosConfig()
    assert config.cache.mode == "both"
    assert config.ocr.enabled is True
    assert config.list.default_limit == 10


def test_save_and_load(tmp_path):
    config_path = tmp_path / "lumos.json"
    config = LumosConfig(data_dir="/tmp/test-lumos")
    with patch("lumos.core.config.CONFIG_PATH", config_path):
        save_config(config)
        loaded = load_config()
        assert loaded.data_dir == "/tmp/test-lumos"


def test_data_dir_env_override():
    config = LumosConfig(data_dir="/default")
    with patch.dict("os.environ", {"LUMOS_DATA_DIR": "/override"}):
        assert str(config.get_data_dir()) == "/override"


def test_deep_update():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    _deep_update(base, {"a": {"b": 10}, "e": 5})
    assert base == {"a": {"b": 10, "c": 2}, "d": 3, "e": 5}
