"""Configuration management for Lumos."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

CONFIG_PATH = Path("~/.config/lumos.json").expanduser()
DEFAULT_DATA_DIR = Path("~/.lumos").expanduser()


class ExtensionConfig(BaseModel):
    shortcut_save_page: str = "Ctrl+D"
    highlight_color: str = "#FFEB3B"
    mini_toolbar: bool = True


class CacheConfig(BaseModel):
    mode: str = "both"  # both | mhtml | readable | none


class OcrConfig(BaseModel):
    enabled: bool = True
    provider: str = "upstage"
    api_key_env: str = "UPSTAGE_API_KEY"
    retry_max: int = 3


class ListConfig(BaseModel):
    default_limit: int = 10


class LumosConfig(BaseModel):
    data_dir: str = str(DEFAULT_DATA_DIR)
    extension: ExtensionConfig = Field(default_factory=ExtensionConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    list: ListConfig = Field(default_factory=ListConfig)

    def get_data_dir(self) -> Path:
        env_override = os.environ.get("LUMOS_DATA_DIR")
        if env_override:
            return Path(env_override).expanduser()
        return Path(self.data_dir).expanduser()

    def items_path(self) -> Path:
        return self.get_data_dir() / "items.jsonl"

    def media_dir(self) -> Path:
        return self.get_data_dir() / "media"

    def cache_dir(self) -> Path:
        return self.get_data_dir() / "cache"


def load_config() -> LumosConfig:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        return LumosConfig.model_validate(data)
    return LumosConfig()


def save_config(config: LumosConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config.model_dump(), indent=2, ensure_ascii=False) + "\n"
    )


def update_config(updates: dict[str, Any]) -> LumosConfig:
    config = load_config()
    data = config.model_dump()
    _deep_update(data, updates)
    config = LumosConfig.model_validate(data)
    save_config(config)
    return config


def _deep_update(base: dict, updates: dict) -> None:
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
