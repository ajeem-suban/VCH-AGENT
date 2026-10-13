"""Validated domain models shared by the VCH backend."""

from datetime import datetime, timezone
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

    def model_post_init(self, __context: object) -> None:
        if self.published_at.tzinfo is None:
            raise ValueError("published_at must include a timezone.")
        object.__setattr__(self, "published_at", self.published_at.astimezone(timezone.utc))


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

    def model_post_init(self, __context: object) -> None:
        if self.extracted_at.tzinfo is None:
            raise ValueError("extracted_at must include a timezone.")
        object.__setattr__(self, "extracted_at", self.extracted_at.astimezone(timezone.utc))


class TranscriptResult(BaseModel):
    """A bounded transcript retrieval result for one YouTube video."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    text: str | None = None
    language_code: str | None = None
    is_generated: bool | None = None
    is_truncated: bool = False
    error_type: str | None = None


class CollectionStatus(str, Enum):
    """Whether a metric source was successfully observed."""

    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class OutcomeStatus(str, Enum):
    PENDING = "pending"
    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class MetricSnapshot(BaseModel):
    """An immutable observation of only the metrics a source supplied."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    content_id: int = Field(gt=0)
    platform: Platform
    observed_at: datetime
    source_published_at: datetime | None = None
    reddit_score: int | None = None
    reddit_comment_count: int | None = Field(default=None, ge=0)
    reddit_upvote_ratio: float | None = Field(default=None, ge=0, le=1)
    youtube_view_count: int | None = Field(default=None, ge=0)
    youtube_like_count: int | None = Field(default=None, ge=0)
    youtube_comment_count: int | None = Field(default=None, ge=0)
    collector_version: str = Field(min_length=1, max_length=100)
    status: CollectionStatus = CollectionStatus.OBSERVED
    error_message: str | None = Field(default=None, max_length=500)
    role: str = Field(default="t0", pattern="^(t0|follow_up)$")

    def model_post_init(self, __context: object) -> None:
        for field in ("observed_at", "source_published_at"):
            value = getattr(self, field)
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"{field} must include a timezone.")
                object.__setattr__(self, field, value.astimezone(timezone.utc))


class PredictionRecord(BaseModel):
    """Future-facing contract; it deliberately does not imply a trained model."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    content_id: int = Field(gt=0)
    predicted_at: datetime
    horizon_hours: int = Field(gt=0)
    predictor_version: str = Field(min_length=1, max_length=100)
    interpretation: str = Field(min_length=1, max_length=500)
    value: float | None = None
    feature_record_id: int | None = Field(default=None, gt=0)
    eligible_for_evaluation: bool = False

    def model_post_init(self, __context: object) -> None:
        if self.predicted_at.tzinfo is None:
            raise ValueError("predicted_at must include a timezone.")
        object.__setattr__(self, "predicted_at", self.predicted_at.astimezone(timezone.utc))


class GrowthMetric(BaseModel):
    """Transparent change calculations for one comparable engagement metric."""

    baseline: int | float
    follow_up: int | float
    absolute_change: int | float
    relative_growth_percent: float | None = None


class OutcomeRecord(BaseModel):
    """A single logical T+N outcome linked to immutable observations."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    content_id: int = Field(gt=0)
    horizon_hours: int = Field(gt=0)
    target_at: datetime
    observed_at: datetime | None = None
    baseline_snapshot_id: int = Field(gt=0)
    follow_up_snapshot_id: int | None = Field(default=None, gt=0)
    status: OutcomeStatus = OutcomeStatus.PENDING
    growth: dict[str, GrowthMetric] = Field(default_factory=dict)
    explanation: str | None = Field(default=None, max_length=500)
    attempted_at: datetime | None = None
    retryable: bool = False

    def model_post_init(self, __context: object) -> None:
        for field in ("target_at", "observed_at", "attempted_at"):
            value = getattr(self, field)
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"{field} must include a timezone.")
                object.__setattr__(self, field, value.astimezone(timezone.utc))
