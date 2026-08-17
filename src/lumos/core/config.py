"""Configuration management for Lumos."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

CONFIG_PATH = Path("~/.config/lumos.json").expanduser()
DEFAULT_DATA_DIR = Path("~/.lumos").expanduser()


class CacheConfig(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["mhtml", "readable"])


class OcrConfig(BaseModel):
    enabled: bool = True
    provider: str = "upstage"
    api_key_env: str = "UPSTAGE_API_KEY"
    retry_max: int = 3


class LlmConfig(BaseModel):
    model: str = "solar-pro4"
    fast_model: str = "solar-mini"  # only used if suggest.fast_below is raised
    api_key_env: str = "UPSTAGE_API_KEY"
    base_url: str = "https://api.upstage.ai/v1"

    def for_length(self, text_len: int, fast_below: int) -> LlmConfig:
        """The model to read a page of this length with.

        Measured on saved pages, the small model is worth about 1-3s on a short
        page and costs the opening of it: its first pick lands at 16-27% of the
        body where the large model starts at 5-11%, run after run. Past ~15k
        chars it also starts paraphrasing instead of quoting, so a third of its
        picks fail the verbatim check and vanish. Off by default (fast_below 0)
        — suggestions are cached per page, so those seconds are paid once.
        """
        if not self.fast_model or fast_below <= 0 or text_len >= fast_below:
            return self
        return self.model_copy(update={"model": self.fast_model})


class ModelsConfig(BaseModel):
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)


class SuggestConfig(BaseModel):
    """Auto-suggested highlights shown when a page loads."""

    enabled: bool = True
    color: str = "#FFF9C4"        # pale yellow — distinct from a real highlight
    ratio: float = 0.06           # ~6% of the body text is offered for underlining
    phrase_chars: int = 150       # observed length of one suggested phrase
    min_phrases: int = 1
    max_phrases: int = 80         # runaway guard for 200k-char pages, not a target
    min_chars: int = 800          # skip pages with too little prose
    max_chars: int = 12000        # LLM reading budget per call
    max_calls: int = 6            # parallel calls before long pages get excerpted
    fast_below: int = 0           # 0 = off; raise (~10000) to trade spread for speed

    def phrase_count(self, text_len: int) -> int:
        """How many passages to offer, scaled to the length of the page.

        A fixed count over-highlights short posts and under-highlights long
        essays; holding the *proportion* steady keeps the density that makes a
        page skimmable regardless of size. This is a ceiling the model is free
        to come in under — an article with three good lines returns three.
        """
        target = round(text_len * self.ratio / self.phrase_chars)
        return max(self.min_phrases, min(self.max_phrases, target))


class ListConfig(BaseModel):
    default_limit: int = 10


class ThemeConfig(BaseModel):
    highlight_color: str = "yellow"         # search match bg: yellow, orange, green, etc.
    selection_color: str = "yellow"         # selected row marker color


class LumosConfig(BaseModel):
    data_dir: str = str(DEFAULT_DATA_DIR)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    list: ListConfig = Field(default_factory=ListConfig)
    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    suggest: SuggestConfig = Field(default_factory=SuggestConfig)

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

    def suggest_dir(self) -> Path:
        return self.cache_dir() / "suggest"


def load_env() -> None:
    """Load ~/.env into os.environ.

    Chrome spawns the native messaging host with a bare environment — no shell
    profile, so API keys exported in .zshrc are invisible. Without this, every
    LLM/OCR call from the extension silently fails on a missing key.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(Path("~/.env").expanduser())
    except ImportError:
        pass


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
