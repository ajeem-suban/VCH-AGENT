"""The first vertical slice: normalized content to persisted features."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol

from .database import VchDatabase
from .domain import (CollectionStatus, ContentItem, GrowthMetric, MetricSnapshot,
                     OutcomeRecord, OutcomeStatus, SemanticFeatures, StoredFeatureRecord)
from .extractors import FeatureExtractionError, SemanticFeatureExtractor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureExtractionBatchResult:
    """Successful feature snapshots and content-specific extraction failures."""

    records: list[StoredFeatureRecord]
    failed_count: int


class FeatureExtractionService:
    """Coordinates validation, extraction, and immutable persistence."""

    def __init__(self, database: VchDatabase, extractor: SemanticFeatureExtractor) -> None:
        self._database = database
        self._extractor = extractor

    def analyze(self, content: ContentItem) -> StoredFeatureRecord:
        """Extract and store features using information available at analysis time."""

        features = self._extractor.extract(content)
        content_id = self._database.store_content(content)
        return self._database.store_features(content_id, features, self._extractor.version)

    def analyze_batch(self, content_items: Iterable[ContentItem]) -> FeatureExtractionBatchResult:
        """Persist a candidate pool and analyze it with one reusable extractor."""

        contents = list(content_items)
        content_ids = self._database.store_content_batch(contents)
        successful_features: list[tuple[int, SemanticFeatures]] = []
        failed_count = 0

        for content, content_id in zip(contents, content_ids, strict=True):
            try:
                successful_features.append((content_id, self._extractor.extract(content)))
            except FeatureExtractionError as error:
                failed_count += 1
                logger.warning("Skipping semantic features for %s: %s", content.external_id, error)

        records = self._database.store_feature_batch(
            successful_features,
            self._extractor.version,
        )
        return FeatureExtractionBatchResult(records=records, failed_count=failed_count)


def initial_metric_snapshot(content: ContentItem, content_id: int, observed_at: datetime | None = None) -> MetricSnapshot:
    """Make an honest T0 observation from metrics already supplied by a harvester."""
    metrics = content.metadata
    timestamp = observed_at or datetime.now(timezone.utc)
    return MetricSnapshot(
        content_id=content_id, platform=content.platform, observed_at=timestamp,
        source_published_at=content.published_at, collector_version="harvest-v1",
        reddit_score=metrics.get("score"), reddit_comment_count=metrics.get("num_comments"),
        reddit_upvote_ratio=metrics.get("upvote_ratio"), youtube_view_count=metrics.get("view_count"),
    )


class MetricCollector(Protocol):
    """Platform adapter returning a fresh snapshot, injected for deterministic tests."""
    def collect(self, content_id: int, baseline: MetricSnapshot) -> MetricSnapshot: ...


@dataclass(frozen=True)
class OutcomeBatchResult:
    observed: int = 0
    pending: int = 0
    unavailable: int = 0
    failed: int = 0


class OutcomeService:
    """Processes due outcomes without using later observations as inputs."""

    def __init__(self, database: VchDatabase, collector: MetricCollector, clock: Callable[[], datetime] | None = None) -> None:
        self._database, self._collector = database, collector
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def collect_due(self, horizon_hours: int) -> OutcomeBatchResult:
        now = self._clock()
        result = OutcomeBatchResult()
        observed = pending = unavailable = failed = 0
        content_ids = {snapshot.content_id for snapshot in self._database.list_metric_snapshots()}
        for content_id in content_ids:
            snapshots = self._database.list_metric_snapshots(content_id)
            baseline = next((item for item in snapshots if item.role == "t0" and item.status == CollectionStatus.OBSERVED), None)
            if baseline is None:
                continue
            outcome = self._database.get_or_create_outcome(content_id, horizon_hours, baseline)
            if outcome.status != OutcomeStatus.PENDING and not (outcome.status == OutcomeStatus.FAILED and outcome.retryable):
                continue
            if now < outcome.target_at:
                pending += 1
                continue
            try:
                follow_up = self._database.store_metric_snapshot(self._collector.collect(content_id, baseline).model_copy(update={"role": "follow_up"}))
                if follow_up.status == CollectionStatus.OBSERVED:
                    outcome = outcome.model_copy(update={"observed_at": follow_up.observed_at, "follow_up_snapshot_id": follow_up.id, "status": OutcomeStatus.OBSERVED, "growth": calculate_growth(baseline, follow_up), "attempted_at": now, "retryable": False})
                    observed += 1
                elif follow_up.status == CollectionStatus.UNAVAILABLE:
                    outcome = outcome.model_copy(update={"observed_at": follow_up.observed_at, "follow_up_snapshot_id": follow_up.id, "status": OutcomeStatus.UNAVAILABLE, "explanation": follow_up.error_message, "attempted_at": now, "retryable": False})
                    unavailable += 1
                else:
                    outcome = outcome.model_copy(update={"observed_at": follow_up.observed_at, "follow_up_snapshot_id": follow_up.id, "status": OutcomeStatus.FAILED, "explanation": follow_up.error_message, "attempted_at": now, "retryable": True})
                    failed += 1
                self._database.complete_outcome(outcome)
            except Exception:
                logger.warning("Follow-up collection failed for content %s", content_id)
                failure = self._database.store_metric_snapshot(MetricSnapshot(content_id=content_id, platform=baseline.platform, observed_at=now, collector_version="outcome-service-v1", status=CollectionStatus.FAILED, error_message="collection_error", role="follow_up"))
                self._database.complete_outcome(outcome.model_copy(update={"observed_at": failure.observed_at, "follow_up_snapshot_id": failure.id, "status": OutcomeStatus.FAILED, "explanation": "collection_error", "attempted_at": now, "retryable": True}))
                failed += 1
        return OutcomeBatchResult(observed, pending, unavailable, failed)


def calculate_growth(baseline: MetricSnapshot, follow_up: MetricSnapshot) -> dict[str, GrowthMetric]:
    """Calculate changes only for metrics present in both immutable snapshots."""
    metrics = ("reddit_score", "reddit_comment_count", "reddit_upvote_ratio", "youtube_view_count", "youtube_like_count", "youtube_comment_count")
    output: dict[str, GrowthMetric] = {}
    for name in metrics:
        before, after = getattr(baseline, name), getattr(follow_up, name)
        if before is None or after is None:
            continue
        delta = after - before
        output[name] = GrowthMetric(baseline=before, follow_up=after, absolute_change=delta, relative_growth_percent=None if before == 0 else (delta / before) * 100)
    return output


def evaluation_summary(database: VchDatabase, now: datetime | None = None) -> dict[str, object]:
    """Describe recorded evidence; never manufacture model-performance claims."""
    snapshots, outcomes = database.list_metric_snapshots(), database.list_outcomes()
    platforms: dict[str, int] = {}
    for snapshot in snapshots:
        platforms[snapshot.platform.value] = platforms.get(snapshot.platform.value, 0) + 1
    status_counts = {status.value: sum(item.status == status for item in outcomes) for status in OutcomeStatus}
    follow_ups = [snapshot for snapshot in snapshots if snapshot.role == "follow_up"]
    initial = [snapshot for snapshot in snapshots if snapshot.role == "t0"]
    by_horizon: dict[str, dict[str, int]] = {}
    by_platform_horizon: dict[str, dict[str, dict[str, int]]] = {}
    late_observations = 0
    excluded_reasons: dict[str, int] = {}
    for item in outcomes:
        counts = by_horizon.setdefault(str(item.horizon_hours), {status.value: 0 for status in OutcomeStatus})
        counts[item.status.value] += 1
        reference = database.content_reference(item.content_id)
        platform = reference[0] if reference else "unknown"
        platform_counts = by_platform_horizon.setdefault(platform, {}).setdefault(str(item.horizon_hours), {status.value: 0 for status in OutcomeStatus})
        platform_counts[item.status.value] += 1
        if item.observed_at and item.observed_at > item.target_at:
            late_observations += 1
        if item.explanation:
            excluded_reasons[item.explanation] = excluded_reasons.get(item.explanation, 0) + 1
    timestamp = now or datetime.now(timezone.utc)
    due_pending = sum(item.status == OutcomeStatus.PENDING and item.target_at <= timestamp for item in outcomes)
    return {"tracked_content": len({snapshot.content_id for snapshot in initial}), "initial_metric_snapshots": len(initial), "follow_up_metric_snapshots": len(follow_ups), "outcomes": len(outcomes), "due_pending_outcomes": due_pending, "late_observations": late_observations, "outcome_status": status_counts, "outcome_status_by_horizon": by_horizon, "outcome_status_by_platform_and_horizon": by_platform_horizon, "excluded_reasons": excluded_reasons, "metric_snapshots_by_platform": platforms, "note": "No trained predictions are evaluated; accuracy metrics are not applicable."}
