"""Validated domain models shared by the VCH backend."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Platform(str, Enum):
    """Supported content sources for the first VCH release."""

    REDDIT = "reddit"
    YOUTUBE = "youtube"


class ContentItem(BaseModel):
    """Normalized content as it existed when VCH analyzed it."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=255)
    platform: Platform
    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    url: HttpUrl
    author_or_channel: str | None = Field(default=None, max_length=255)
    community_or_channel: str | None = Field(default=None, max_length=255)
    published_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticFeatures(BaseModel):
    """Candidate semantic signals produced by a feature extractor.

    These are inputs for future experiments, not a viral-probability score.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    hook: int = Field(ge=1, le=10)
    emotion: int = Field(ge=1, le=10)
    utility: int = Field(ge=1, le=10)
    surprise: int = Field(ge=1, le=10)
    controversy: int = Field(ge=1, le=10)


class StoredFeatureRecord(BaseModel):
    """A persisted semantic-feature extraction."""

    id: int
    content_id: int
    extracted_at: datetime
    extractor_version: str
    features: SemanticFeatures


class TranscriptResult(BaseModel):
    """A bounded transcript retrieval result for one YouTube video."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    text: str | None = None
    language_code: str | None = None
    is_generated: bool | None = None
    is_truncated: bool = False
    error_type: str | None = None
