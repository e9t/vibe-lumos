"""Pydantic models for Lumos items."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ItemType(str, Enum):
    PAGE = "page"
    HIGHLIGHT = "highlight"
    IMAGE = "image"


class SourceVia(str, Enum):
    WEB = "web"
    KINDLE = "kindle"
    X = "x"


class Source(BaseModel):
    via: SourceVia = SourceVia.WEB
    book: Optional[str] = None
    author: Optional[str] = None
    page: Optional[int] = None
    location: Optional[str] = None
    original_html: Optional[str] = None


class Location(BaseModel):
    xpath: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    text_fingerprint: Optional[str] = None


class Cache(BaseModel):
    mhtml: Optional[str] = None
    readable: Optional[str] = None


def generate_id() -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    random_part = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"lm_{date_str}_{random_part}"


class Item(BaseModel):
    id: str = Field(default_factory=generate_id)
    type: ItemType
    url: str
    title: str
    text: Optional[str] = None
    note: Optional[str] = None
    media: Optional[str] = None
    ocr_text: Optional[str] = None
    cache: Optional[Cache] = None
    source: Source = Field(default_factory=Source)
    location: Optional[Location] = None
    priority: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
