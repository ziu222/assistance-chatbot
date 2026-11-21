"""Kiểu dữ liệu model trả về (structured output). Thiết kế bước 2 §6.5."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClassifyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intents: list[str] = Field(max_length=5)
    confidence_note: str = Field(max_length=300)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["faq", "doc", "product"]
    id: str = Field(max_length=64)


class DraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(max_length=1200)  # tiếng Việt, ngắn
    sources: list[Source] = Field(max_length=10)
    needs_human: bool
    reason: Literal["", "no_source", "policy", "discount", "order", "complaint", "unclear"]
