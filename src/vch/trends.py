"""Evidence-only trend comparisons over immutable metric observations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .database import VchDatabase
from .domain import CollectionStatus, MetricSnapshot, Platform
from .service import MetricCollector

METRICS = ("reddit_score", "reddit_comment_count", "reddit_upvote_ratio", "youtube_view_count", "youtube_like_count", "youtube_comment_count")


def compare_metric(name: str, earlier: MetricSnapshot, later: MetricSnapshot) -> dict[str, object]:
    """Compare one metric only when both timestamped observations support it."""
    if later.observed_at <= earlier.observed_at:
        return {"metric": name, "available": False, "reason": "invalid_timestamp_order"}
    before, after = getattr(earlier, name), getattr(later, name)
    if before is None or after is None:
        return {"metric": name, "available": False, "reason": "missing_metric"}
    change = after - before
    return {"metric": name, "available": True, "earlier_value": before, "later_value": after,
            "absolute_change": change, "percentage_change": None if before == 0 else change / before * 100,
            "percentage_reason": "zero_baseline" if before == 0 else None,
            "earlier_observed_at": earlier.observed_at.isoformat(), "later_observed_at": later.observed_at.isoformat(),
            "elapsed_hours": (later.observed_at - earlier.observed_at).total_seconds() / 3600,
            "provenance": [earlier.collector_version, later.collector_version]}


def trend_report(database: VchDatabase, window_hours: int, now: datetime | None = None) -> dict[str, object]:
    """Build deterministic, auditable post and topic summaries from saved observations."""
    timestamp = now or datetime.now(timezone.utc)
    cutoff = timestamp - timedelta(hours=window_hours)
    posts: list[dict[str, object]] = []
    for content in database.list_content_references():
        snapshots = [item for item in database.list_metric_snapshots(int(content["id"])) if item.status == CollectionStatus.OBSERVED]
        in_window = [item for item in snapshots if item.observed_at >= cutoff]
        record: dict[str, object] = {"content_id": content["id"], "platform": content["platform"], "external_id": content["external_id"], "url": content["url"], "topic": content["community_or_channel"], "observation_count": len(in_window), "limitations": []}
        if len(in_window) < 2:
            record["limitations"].append("insufficient_observations")
            record["comparisons"] = []
        else:
            earlier, later = in_window[0], in_window[-1]
            record["comparisons"] = [compare_metric(metric, earlier, later) for metric in METRICS]
            record["earliest_observed_at"] = earlier.observed_at.isoformat()
            record["latest_observed_at"] = later.observed_at.isoformat()
            if len(in_window) == 2:
                record["limitations"].append("two_observations_only")
        posts.append(record)
    topics: dict[str, dict[str, object]] = {}
    for post in posts:
        topic = str(post["topic"] or "unclassified")
        summary = topics.setdefault(topic, {"topic": topic, "tracked_posts": 0, "comparable_posts": 0, "increasing_posts": 0, "decreasing_posts": 0, "stable_posts": 0})
        summary["tracked_posts"] = int(summary["tracked_posts"]) + 1
        available = [item for item in post["comparisons"] if item.get("available")]
        if not available:
            continue
        summary["comparable_posts"] = int(summary["comparable_posts"]) + 1
        direction = int(available[0]["absolute_change"] > 0) - int(available[0]["absolute_change"] < 0)
        key = "increasing_posts" if direction > 0 else "decreasing_posts" if direction < 0 else "stable_posts"
        summary[key] = int(summary[key]) + 1
    for summary in topics.values():
        comparable, increasing = int(summary["comparable_posts"]), int(summary["increasing_posts"])
        summary["assessment"] = "increasing activity observed" if comparable >= 2 and increasing >= 2 else "insufficient evidence"
    return {"generated_at": timestamp.isoformat(), "window_hours": window_hours, "posts": posts, "topics": list(topics.values()), "limitations": ["Observations are VCH collection-time measurements; no provider historical totals are inferred."]}


@dataclass(frozen=True)
class CycleResult:
    collected: int = 0
    skipped: int = 0
    failed: int = 0


class TrendCollectionService:
    """A manually invoked, scheduler-friendly twelve-hour collection cycle."""
    def __init__(self, database: VchDatabase, collector: MetricCollector, clock: Callable[[], datetime] | None = None) -> None:
        self._database, self._collector = database, collector
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def collect_due(self, interval_hours: int = 12) -> CycleResult:
        now, collected, skipped, failed = self._clock(), 0, 0, 0
        for content in self._database.list_content_references():
            if content["platform"] != Platform.REDDIT.value:
                skipped += 1
                continue
            content_id = int(content["id"])
            snapshots = self._database.list_metric_snapshots(content_id)
            baseline = next((item for item in snapshots if item.status == CollectionStatus.OBSERVED), None)
            latest_successful = next((item for item in reversed(snapshots) if item.status == CollectionStatus.OBSERVED), None)
            if baseline is None or (latest_successful and now - latest_successful.observed_at < timedelta(hours=interval_hours)):
                skipped += 1
                continue
            try:
                snapshot = self._collector.collect(content_id, baseline).model_copy(update={"role": "follow_up"})
                self._database.store_metric_snapshot(snapshot)
                if snapshot.status == CollectionStatus.FAILED:
                    failed += 1
                else:
                    collected += 1
            except Exception:
                failed += 1
        return CycleResult(collected, skipped, failed)
